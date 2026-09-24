"""Audio recall: the last 30 seconds of what this computer played, as text.

Three threads, none of which the tool call ever waits on except the model:

  capture    records the default output device through WASAPI loopback (the
             `soundcard` package) into a rolling 30 s buffer. Output only —
             this never opens a microphone. It follows the default device, so
             plugging in headphones moves it with you.
  refresher  every few seconds, if anything audible is in the window,
             transcribes the whole window and rewrites the transcript file.
  loader     loads the Whisper model once, in the background, so the MCP
             handshake is not held up by it.

Transcribing the whole window each time, rather than stitching 5 s pieces
together, is what keeps words from being cut in half at the seams; with
faster-whisper's base.en model a 30 s window takes well under a second on a
laptop CPU, so it is affordable. A recall does one more fresh pass, so what
the agent reads is current to the moment it asked.

Nothing is sent anywhere: the audio stays in memory and is dropped as it
ages out of the window, and the transcript file only ever holds the last
30 seconds' worth.
"""
import collections
import os
import sys
import tempfile
import threading
import time
import warnings

import numpy as np

WINDOW_SECONDS = 30.0
SAMPLE_RATE = 16000                 # what Whisper wants; WASAPI resamples for us
BLOCK_FRAMES = SAMPLE_RATE // 10    # 100 ms per read
REFRESH_SECONDS = 5.0
DEVICE_CHECK_SECONDS = 3.0
# Loopback of true silence is exact zeros; this is just above dither.
SILENCE_PEAK = 1e-3
MODEL_WAIT_SECONDS = 60.0

DEFAULT_MODEL = "base.en"


def model_location():
    """The Whisper model to load: MARK6_WHISPER_MODEL if set, else the copy
    bin\\bootstrap.ps1 put in runtime\\models, else the name alone, which
    faster-whisper fetches from Hugging Face on first use."""
    override = os.environ.get("MARK6_WHISPER_MODEL")
    if override:
        return override
    bundled = os.path.join(sys.prefix, "models", f"whisper-{DEFAULT_MODEL}")
    if os.path.isfile(os.path.join(bundled, "model.bin")):
        return bundled
    return DEFAULT_MODEL


class AudioRecall:
    def __init__(self, transcript_path):
        self.transcript_path = transcript_path
        self._chunks = collections.deque()      # (monotonic end time, samples)
        self._chunks_lock = threading.Lock()
        self._last_heard = None                 # monotonic time of last sound
        self._stop = threading.Event()

        self._model = None
        self._model_error = None
        self._model_ready = threading.Event()
        self._model_lock = threading.Lock()     # one transcription at a time

        self.device = None
        self.capture_error = None

    # ── lifecycle ────────────────────────────────────────────

    def start(self):
        for target in (self._load_model, self._capture, self._refresh):
            threading.Thread(target=target, daemon=True).start()

    def stop(self):
        self._stop.set()

    # ── model ────────────────────────────────────────────────

    def _load_model(self):
        try:
            from faster_whisper import WhisperModel
            where = model_location()
            print(f"[audio] loading Whisper model {where}")
            self._model = WhisperModel(where, device="cpu", compute_type="int8")
            print("[audio] model ready")
        except Exception as e:                            # noqa: BLE001
            self._model_error = f"{type(e).__name__}: {e}"
            print(f"[audio] model failed to load: {self._model_error}")
        finally:
            self._model_ready.set()

    def _transcribe(self, audio):
        with self._model_lock:
            segments, _info = self._model.transcribe(
                audio, vad_filter=True, beam_size=5,
                # Each pass sees the window fresh; carrying text over from a
                # previous pass is how Whisper talks itself into repeating a
                # phrase that is no longer there.
                condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segments).strip()

    # ── capture ──────────────────────────────────────────────

    def _capture(self):
        import soundcard as sc
        # "data discontinuity in recording" fires on every glitch or device
        # hiccup and says nothing a person can act on.
        warnings.simplefilter("ignore", getattr(sc, "SoundcardRuntimeWarning", Warning))

        backoff = 1.0
        while not self._stop.is_set():
            try:
                speaker = sc.default_speaker()
                loopback = sc.get_microphone(id=str(speaker.id), include_loopback=True)
                with loopback.recorder(samplerate=SAMPLE_RATE, channels=1,
                                       blocksize=BLOCK_FRAMES) as recorder:
                    self.device, self.capture_error = speaker.name, None
                    print(f"[audio] listening to {speaker.name}")
                    backoff = 1.0
                    checked = time.monotonic()
                    while not self._stop.is_set():
                        data = recorder.record(numframes=BLOCK_FRAMES)
                        self._add(np.ascontiguousarray(data[:, 0], dtype=np.float32))
                        now = time.monotonic()
                        if now - checked >= DEVICE_CHECK_SECONDS:
                            checked = now
                            if sc.default_speaker().id != speaker.id:
                                print("[audio] default output changed; following it")
                                break
            except Exception as e:                        # noqa: BLE001
                self.capture_error = f"{type(e).__name__}: {e}"
                print(f"[audio] capture failed ({self.capture_error}); "
                      f"retrying in {backoff:.0f}s")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def _add(self, samples):
        now = time.monotonic()
        with self._chunks_lock:
            self._chunks.append((now, samples))
            if samples.size and float(np.abs(samples).max()) > SILENCE_PEAK:
                self._last_heard = now
            while self._chunks and self._chunks[0][0] < now - WINDOW_SECONDS:
                self._chunks.popleft()

    def _window(self):
        """The audio of the last WINDOW_SECONDS, and whether any of it was
        audible."""
        now = time.monotonic()
        with self._chunks_lock:
            parts = [s for t, s in self._chunks if t >= now - WINDOW_SECONDS]
            audible = (self._last_heard is not None
                       and self._last_heard >= now - WINDOW_SECONDS)
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        return audio, audible

    # ── transcript ───────────────────────────────────────────

    def _refresh(self):
        self._model_ready.wait()
        if self._model is None:
            return
        wrote_empty = False
        while not self._stop.wait(REFRESH_SECONDS):
            audio, audible = self._window()
            if not audible:
                if not wrote_empty:
                    self._write("")
                    wrote_empty = True
                continue
            wrote_empty = False
            try:
                self._write(self._transcribe(audio))
            except Exception as e:                        # noqa: BLE001
                print(f"[audio] background transcription failed: {e}")

    def _write(self, text):
        directory = os.path.dirname(self.transcript_path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".audio-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text + ("\n" if text else ""))
            os.replace(tmp, self.transcript_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def recall(self):
        """The transcript of the last 30 seconds, freshly made. Raises
        RuntimeError with a sentence the model can pass on if it cannot."""
        if not self._model_ready.wait(MODEL_WAIT_SECONDS):
            raise RuntimeError("The speech model is still loading; try again in a moment.")
        if self._model is None:
            raise RuntimeError(f"The speech model could not be loaded: {self._model_error}")

        audio, audible = self._window()
        if audio.size == 0:
            raise RuntimeError("Nothing has been recorded yet"
                               + (f": {self.capture_error}" if self.capture_error else
                                  " — audio recall only just started."))
        if not audible:
            self._write("")
            return (f"Nothing was playing on this computer in the last "
                    f"{WINDOW_SECONDS:.0f} seconds (silence on {self.device}).")

        text = self._transcribe(audio)
        self._write(text)
        if not text:
            return (f"Sound played in the last {WINDOW_SECONDS:.0f} seconds, but "
                    f"no speech was recognised in it (music or sound effects, most likely).")
        return text
