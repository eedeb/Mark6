"""Mark 6 — the window.

A small tkinter front end over exactly the same pieces the terminal build
uses: auth.login to pair, config for the server list, Relay to connect. It
owns no logic of its own beyond keeping those off the Tk thread.

Anything slow (pairing, starting servers, the relay's long-poll) runs on a
worker thread and reports back through a queue that the Tk loop drains every
100ms — tkinter is not thread-safe, so no worker ever touches a widget.
"""
import os
import queue
import shlex
import threading
import traceback
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import auth, config, httpjson
from .relay import Relay, RelayError

POLL_MS = 100


def split_command_line(line):
    """A command line typed into a box, as an argv array. On Windows that is
    CommandLineToArgvW's job — shlex would eat the backslashes in every path."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        fn = ctypes.windll.shell32.CommandLineToArgvW
        fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        fn.restype = ctypes.POINTER(wintypes.LPWSTR)
        n = ctypes.c_int()
        argv = fn(line, ctypes.byref(n))
        if not argv:
            return []
        try:
            return [argv[i] for i in range(n.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
    return shlex.split(line)


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.relay = None
        self.pairing = False
        self.server_vars = {}

        root.title("Mark 6")
        # Tk scales fonts to the display's DPI but not a geometry given in
        # pixels, so the window size is scaled by hand (1.333 = 96 DPI).
        k = float(root.tk.call("tk", "scaling")) / (96 / 72)
        root.geometry(f"{int(460 * k)}x{int(560 * k)}")
        root.minsize(int(400 * k), int(460 * k))
        self.wrap = int(400 * k)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        style = ttk.Style()
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#666")
        style.configure("Code.TLabel", font=("Consolas", 18, "bold"))
        style.configure("Big.TButton", font=("Segoe UI", 11), padding=8)

        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Mark 6", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Lend this computer's tools to your FreeClaw agent.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        # ── account ──
        self.account_box = ttk.LabelFrame(outer, text="Account", padding=10)
        self.account_box.pack(fill="x")

        # ── servers ──
        servers_box = ttk.LabelFrame(outer, text="Tools on this computer", padding=10)
        servers_box.pack(fill="x", pady=(12, 0))
        self.server_list = ttk.Frame(servers_box)
        self.server_list.pack(fill="x")
        self.add_button = ttk.Button(servers_box, text="+ Add server...",
                                     command=self.add_server)
        self.add_button.pack(anchor="w", pady=(8, 0))

        # ── connect ──
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(14, 0))
        self.connect_button = ttk.Button(row, text="Connect", style="Big.TButton",
                                         command=self.toggle_connection)
        self.connect_button.pack(side="left")
        self.status = ttk.Label(row, text="Not connected", style="Muted.TLabel")
        self.status.pack(side="left", padx=12)

        # ── activity ──
        self.log_box = ScrolledText(outer, height=8, state="disabled",
                                    font=("Consolas", 9), relief="flat",
                                    background="#f4f4f4")
        self.log_box.pack(fill="both", expand=True, pady=(12, 0))

        self.render_account()
        self.render_servers()
        self.root.after(POLL_MS, self.drain)

    # ── plumbing ─────────────────────────────────────────────

    def emit(self, kind, *payload):
        """Called from worker threads."""
        self.events.put((kind, payload))

    def drain(self):
        """Deliver whatever the workers have queued, then re-arm.

        Every handler runs inside its own try, and the re-arm is in a finally,
        because neither is optional: an exception escaping here would leave
        `after` never called again, and the pump is the only route a worker
        has to the window. The window would stay open and answer clicks while
        silently showing nothing — no log lines, no "connected", no pairing —
        and under pythonw.exe there is no console for the traceback to reach.
        A failed handler is a bug to read, so it goes in the log box where it
        can actually be seen."""
        try:
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    return
                try:
                    getattr(self, f"on_{kind}")(*payload)
                except Exception:                         # noqa: BLE001
                    self.log(f"[internal] {kind} handler failed:\n"
                             + traceback.format_exc())
        finally:
            self.root.after(POLL_MS, self.drain)

    def log(self, line):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line.rstrip("\n") + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def on_log(self, line):
        self.log(line)

    @property
    def connected(self):
        return self.relay is not None

    # ── account ──────────────────────────────────────────────

    def render_account(self, code=None, uri=None):
        for child in self.account_box.winfo_children():
            child.destroy()
        cfg = config.load()

        if code:
            ttk.Label(self.account_box, text="Open this page while signed in to "
                      "your Mark 6 account, and enter the code:",
                      wraplength=self.wrap).pack(anchor="w")
            ttk.Label(self.account_box, text=code, style="Code.TLabel").pack(
                anchor="w", pady=6)
            row = ttk.Frame(self.account_box)
            row.pack(anchor="w")
            ttk.Button(row, text="Open page",
                       command=lambda: webbrowser.open(uri)).pack(side="left")
            ttk.Button(row, text="Copy code",
                       command=lambda: self.copy(code)).pack(side="left", padx=6)
            ttk.Label(self.account_box, text="Waiting for you to approve it...",
                      style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        elif self.pairing:
            ttk.Label(self.account_box, text="Asking for a pairing code...").pack(anchor="w")
        elif config.signed_in(cfg):
            row = ttk.Frame(self.account_box)
            row.pack(fill="x")
            ttk.Label(row, text=f"Paired as \"{cfg['device_name']}\" on "
                      f"{cfg['account']}").pack(side="left")
            ttk.Button(row, text="Forget", command=self.forget).pack(side="right")
        else:
            row = ttk.Frame(self.account_box)
            row.pack(fill="x")
            ttk.Label(row, text="This computer is not paired yet.").pack(side="left")
            ttk.Button(row, text="Pair this computer",
                       command=self.pair).pack(side="right")

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def pair(self):
        self.pairing = True
        self.render_account()

        def work():
            try:
                cfg = auth.login(on_code=lambda c, u: self.emit("code", c, u))
                self.emit("paired", cfg)
            except (auth.AuthError, httpjson.Unreachable) as e:
                self.emit("pair_failed", str(e))

        threading.Thread(target=work, daemon=True).start()

    def on_code(self, code, uri):
        self.render_account(code, uri)
        webbrowser.open(uri)

    def on_paired(self, cfg):
        self.pairing = False
        self.render_account()
        self.log(f"Paired as \"{cfg['device_name']}\" on account {cfg['account']}.")

    def on_pair_failed(self, message):
        self.pairing = False
        self.render_account()
        messagebox.showerror("Mark 6", f"Pairing failed:\n\n{message}")

    def forget(self):
        if not messagebox.askyesno(
                "Mark 6", "Forget the pairing on this computer?\n\n"
                "To stop it being used for good, also Unpair it from your "
                "dashboard."):
            return
        if self.connected:
            self.disconnect()
        auth.logout()
        self.render_account()

    # ── servers ──────────────────────────────────────────────

    def render_servers(self):
        for child in self.server_list.winfo_children():
            child.destroy()
        self.server_vars = {}
        cfg = config.load()
        state = "disabled" if self.connected else "normal"

        if not cfg["servers"]:
            ttk.Label(self.server_list, text="None yet.",
                      style="Muted.TLabel").pack(anchor="w")

        for server in cfg["servers"]:
            row = ttk.Frame(self.server_list)
            row.pack(fill="x", pady=2)
            var = tk.BooleanVar(value=bool(server.get("enabled")))
            self.server_vars[server["name"]] = var
            ttk.Checkbutton(row, text=server["name"], variable=var, state=state,
                            command=lambda n=server["name"]: self.toggle_server(n)
                            ).pack(side="left")
            ttk.Button(row, text="Remove", width=8, state=state,
                       command=lambda n=server["name"]: self.remove_server(n)
                       ).pack(side="right")
            detail = server.get("description") or " ".join(
                [server["command"], *(server.get("args") or [])])
            ttk.Label(self.server_list, text=detail, style="Muted.TLabel",
                      wraplength=self.wrap).pack(anchor="w", padx=(22, 0))

        self.add_button.configure(state=state)

    def toggle_server(self, name):
        cfg = config.load()
        for server in cfg["servers"]:
            if server["name"] == name:
                server["enabled"] = self.server_vars[name].get()
        config.save(cfg)

    def remove_server(self, name):
        if not messagebox.askyesno("Mark 6", f"Remove '{name}'?"):
            return
        cfg = config.load()
        cfg["servers"] = [s for s in cfg["servers"] if s["name"] != name]
        config.save(cfg)
        self.render_servers()

    def add_server(self):
        AddServerDialog(self.root, on_added=self.render_servers)

    # ── connection ───────────────────────────────────────────

    def toggle_connection(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        cfg = config.load()
        if not config.signed_in(cfg):
            messagebox.showinfo("Mark 6", "Pair this computer first.")
            return
        if not any(s.get("enabled") for s in cfg["servers"]):
            if not messagebox.askyesno(
                    "Mark 6", "No tools are ticked, so your agent will see "
                    "nothing. Connect anyway?"):
                return

        relay = Relay(log=lambda line: self.emit("log", line),
                      on_connected=lambda n: self.emit("connected", relay, n))
        self.relay = relay
        self.connect_button.configure(text="Disconnect")
        self.status.configure(text="Starting servers...")
        self.render_servers()
        self.log("Connecting...")

        def work():
            # Relay.start only returns by raising, or once stop() is called:
            # after the handshake it sits in its long-poll loop for good.
            try:
                relay.start()
            except RelayError as e:
                self.emit("dropped", relay, str(e))
            except httpjson.Unreachable as e:
                self.emit("dropped", relay, f"Could not reach the server: {e}")
            except Exception as e:                        # noqa: BLE001
                self.emit("dropped", relay, f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()

    def on_connected(self, relay, count):
        if relay is not self.relay:
            return
        self.status.configure(
            text=f"Connected: {count} tool{'' if count == 1 else 's'} shared")

    def on_dropped(self, relay, message):
        threading.Thread(target=relay.stop, daemon=True).start()
        if relay is not self.relay:
            return                            # an old one, already replaced
        self.relay = None
        self.connect_button.configure(text="Connect")
        self.status.configure(text="Not connected")
        self.render_servers()
        self.render_account()
        self.log(message)
        messagebox.showerror("Mark 6", message)

    def disconnect(self):
        relay, self.relay = self.relay, None
        if relay:
            # Stopping the servers can take a few seconds each; keep that off
            # the Tk thread so the window does not freeze.
            threading.Thread(target=relay.stop, daemon=True).start()
        self.connect_button.configure(text="Connect")
        self.status.configure(text="Not connected")
        self.render_servers()
        self.log("Disconnected. Your agent no longer sees these tools.")

    def on_close(self):
        if self.connected:
            self.relay.stop()
        self.root.destroy()


class AddServerDialog:
    def __init__(self, parent, on_added):
        self.on_added = on_added
        self.win = win = tk.Toplevel(parent)
        win.title("Add a server")
        win.transient(parent)
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Name").grid(row=0, column=0, sticky="w")
        self.name = ttk.Entry(frame, width=48)
        self.name.grid(row=1, column=0, sticky="we", pady=(0, 8))
        ttk.Label(frame, text="Command").grid(row=2, column=0, sticky="w")
        self.command = ttk.Entry(frame, width=48)
        self.command.grid(row=3, column=0, sticky="we")
        ttk.Label(frame, style="Muted.TLabel", wraplength=380, text=(
            "For example: npx -y @modelcontextprotocol/server-filesystem "
            "C:\\Users\\me\\notes\n\nIt starts switched off.")).grid(
            row=4, column=0, sticky="w", pady=(6, 10))

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, sticky="e")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Add", command=self.submit).pack(side="right", padx=6)

        win.bind("<Return>", lambda _e: self.submit())
        win.bind("<Escape>", lambda _e: win.destroy())
        self.name.focus_set()
        win.grab_set()

    def submit(self):
        name = self.name.get().strip()
        argv = split_command_line(self.command.get().strip())
        if not config.NAME_RE.match(name):
            messagebox.showerror("Mark 6", "A name can be letters, numbers, dash "
                                 "and underscore, up to 32 characters.", parent=self.win)
            return
        if not argv:
            messagebox.showerror("Mark 6", "Give a command to run.", parent=self.win)
            return
        cfg = config.load()
        if any(s["name"] == name for s in cfg["servers"]):
            messagebox.showerror("Mark 6", f"There is already a server called '{name}'.",
                                 parent=self.win)
            return
        cfg["servers"].append({"name": name, "command": argv[0], "args": argv[1:],
                               "env": {}, "enabled": False})
        config.save(cfg)
        self.win.destroy()
        self.on_added()


def main():
    if os.name == "nt":
        # Without this Windows bitmap-stretches the whole window on a scaled
        # display, which is what makes Tk apps look blurry there.
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
