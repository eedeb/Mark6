"""Mark 6's own MCP server: ways to reach this computer that no off-the-shelf
server covers. Bundled like the computer-use server, and switched off like
everything else until someone ticks it.

For now it has one tool, recall_recent_audio. New tools go here as
@server.tool functions; each one's description is what the model reads when
deciding whether to call it, so it says what the tool is *for*.
"""
import os
import sys

from mark6 import config

from .audio import WINDOW_SECONDS, AudioRecall
from .stdio_server import StdioServer, ToolError

VERSION = "0.1.0"


def transcript_path():
    return os.path.join(config.config_dir(), "audio-recall.txt")


def build():
    server = StdioServer("mark6-tools", VERSION)
    recall = AudioRecall(transcript_path())

    @server.tool(
        "recall_recent_audio",
        f"Transcript of the last {WINDOW_SECONDS:.0f} seconds of audio this "
        "computer played through its speakers: videos, calls, podcasts, "
        "anything with speech in it. Transcribed locally. Use it when the "
        "person refers to something they just heard (\"what did she just "
        "say?\", \"write down that number\"). It hears the computer's output "
        "only, never the microphone, so it cannot hear the person speaking in "
        "the room.")
    def recall_recent_audio():
        try:
            return recall.recall()
        except RuntimeError as e:
            raise ToolError(str(e)) from e

    return server, recall


def main():
    server, recall = build()
    recall.start()
    try:
        server.serve()           # returns when Mark 6 closes our stdin
    finally:
        recall.stop()
    # The recorder thread can be parked inside a WASAPI read; there is
    # nothing left to flush, so do not wait for it.
    sys.stderr.flush()
    os._exit(0)
