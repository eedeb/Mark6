# Mark 6

The desktop half of [Mark 6](https://freeclaw.eedeb.dev/host). It runs MCP
servers on your own computer and lends their tools to your hosted agent, for
as long as it is running.

Double-click `run.bat` and it opens a small window: pair, tick the tools
you want to lend, press **Connect**. The same app is also a terminal build
(`run.bat <command>`) for anyone who would rather type.

It comes with one MCP server already installed:
[computer-use-mcp](https://github.com/kanishka089/computer-use-mcp)
("realhands"), which lets your agent see your screen and drive your real
mouse and keyboard. Like everything else, it starts switched off.

## What it is for

Your agent lives on a server. Plenty of the useful MCP servers do not: they
read your files, drive an app you have open, talk to something on your own
network. Those cannot be moved to the cloud and should not be. So instead of
moving the server to the agent, this lends the agent the server.

```
your agent  ──▶  freeclaw.eedeb.dev  ◀──  Mark 6  ──▶  your MCP servers
   (cloud)            (relay)            (this app)      (this computer)
```

The app dials **out** and holds the connection open. Nothing on the server
ever connects to your computer, so there is no port to forward and no firewall
rule to add.

Mark 6 tier only. On the stock plan the pairing page refuses, and the relay
does not answer.

## Install — Windows

Double-click `run.bat`.

**Nothing else to install.** The first run (a minute or so) fetches into
`runtime\` beside this file:

- a private copy of Python (python.org's own embeddable distribution),
- tkinter for the window (python.org's `tcltk.msi`, unpacked with an
  administrative extract, so nothing is registered or installed system-wide),
- pip, and the bundled computer-use server (`realhands`, from PyPI).

It never touches anything already on the machine or on PATH — the same trick
[FreeClaw's own Windows installer](https://github.com/eedeb/FreeClaw) uses for
itself. Every run after the first starts instantly, with no network involved
at all. An older `runtime\` from before the window existed is topped up in
place, not downloaded again.

## Install — macOS / Linux

Python 3.9 or newer, already on the machine, is all it needs:

```
python3 src/cli.py login
```

(No `run.bat` here — that script exists specifically to fetch a private
Python for a machine that has none. If you already have Python, there is
nothing to bootstrap.)

## Use — the window

1. **Pair this computer.** A code appears and your browser opens the pairing
   page; enter the code there while signed in to your Mark 6 account.
2. **Tick the tools** your agent may use. `computer` is the bundled
   computer-use server; **+ Add server...** takes any other stdio MCP server
   by command line.
3. **Connect.** Your agent has those tools until you press Disconnect or
   close the window. The box underneath shows every call as it happens.

Ticks are locked while connected; disconnect, change them, connect again.

### Computer use

With `computer` ticked, your agent can take screenshots and move the real
mouse and keyboard — anything you could do sitting there. It is fully
autonomous once connected, so it comes with its own brakes:

- **Ctrl+Alt+Q** hard-stops it.
- Slamming the mouse into the **top-left corner** aborts the current action.
- A small **STOP AGENT** overlay appears top-right while it is acting.

Leave it off unless you are about to use it, and don't leave it connected
unattended near anything that can spend money, send messages, or delete data.
Its settings are in the realhands README (all optional).

## Use — the terminal

```
run.bat login                  pair this computer with your Mark 6 account
run.bat add <name> -- <cmd>    add a local MCP server (starts switched off)
run.bat test <name>            start it locally and list its tools
run.bat enable <name>          let your agent use it
run.bat run                    connect, and stay connected
run.bat list                   what is configured, and what is on
run.bat                        (no command) open the window
```

(On macOS/Linux, replace `run.bat` with `python3 src/cli.py` throughout.)

A first run, end to end:

```
> run.bat login

  Open this page while signed in to your Mark 6 account:
      https://freeclaw.eedeb.dev/host/device

  and enter this code:
      74C3-BZUK

  Waiting for you to approve it...

Paired as "Work laptop" on account eedeb.

> run.bat add files -- npx -y @modelcontextprotocol/server-filesystem C:\Users\me\notes
Added 'files': npx -y @modelcontextprotocol/server-filesystem C:\Users\me\notes
It is OFF. Switch it on with:  run.bat enable files

> run.bat test files
It works. 11 tools:
  read_file                    Read the complete contents of a file...
  ...

> run.bat enable files
'files' is now ON.

> run.bat run
  · files_read_file
  · files_write_file
  ...
11 tools ready for eedeb.
Connected. Leave this running — closing it takes the tools away.

→ files_read_file
← files_read_file (14ms)
```

Your agent now sees `mcp_desktop_files_read_file` and the rest. Close the app
and they are gone; it is told so plainly and carries on without them.

## The safety model, in one paragraph

**Everything starts switched off.** `run.bat add` records a server; it does
not turn it on. Only the servers you `enable` are ever published, and only
while `run.bat run` is in the foreground.

That matters more here than it would elsewhere, because the trust runs the
unusual way round. Everywhere else in this product the server is protecting
itself from you. Here your agent — which reads the open web, and can therefore
be argued with by a web page — is being handed a tool that executes on your
personal machine. The list of things it can reach has to be short, chosen, and
chosen *here* rather than there. Pick servers the way you would pick what to
leave unlocked, and prefer read-only ones.

Nothing about a paired computer is permanent: **Unpair** on your dashboard
stops it on the app's next poll, whether or not the machine is reachable.

## How it works

`src/mark6/mcp/client.py` speaks the stdio MCP transport — newline-delimited
JSON-RPC over a child process's stdin and stdout. Children are spawned with
`shell=False`, and on Windows the `.cmd`/`.exe` extension is resolved by hand
before launching, so that `npx` works without a shell ever seeing your
arguments (`Popen`, unlike `cmd.exe`, does not try each `PATHEXT` extension
against `PATH` on its own — the same footgun Node.js has, for the identical
underlying Win32 reason).

`src/mark6/mcp/pool.py` holds the enabled servers and flattens their tools
into one list, namespaced `<server>_<tool>` so two servers cannot collide.
Each description is prefixed with where it runs, so the model — and you,
reading the transcript — can tell a tool on your laptop from one on the
internet.

`src/mark6/gui.py` is the window: tkinter, over exactly the same modules as
the terminal build. Pairing and the relay run on worker threads and report
back through a queue the Tk loop drains, so nothing slow ever freezes it.

`src/mark6/relay.py` is the loop: publish the tool list, hold a long-poll
open, run whatever comes back, post the result. A dropped connection backs
off and retries; being unpaired is the one error it stops for.

On the server side this arrives as an ordinary HTTP MCP server in the
account's list, so FreeClaw needed no new code to use it at all — see
`FreeBusiness/relay/server.py`.

**Zero third-party dependencies anywhere in the app** — stdlib only
(`subprocess`, `threading`, `urllib.request`, `json`). That is what makes the
Windows bootstrap this simple: no pip, no `requirements.txt`, no build step —
just an interpreter and this checkout.

## Testing without a real MCP server

`test/echo_server.py` is a minimal one with four tools: one that works, one
that reports the machine it is on, one that always fails, and one that
sleeps, for exercising the error and timeout paths. A server's command is run
from this app's own folder regardless of where you launched `run.bat` from
(`src/mark6/mcp/client.py: APP_ROOT`), so a relative path here always means
"relative to this checkout."

Windows, using the private interpreter `run.bat` already bootstrapped:

```
run.bat add echo -- runtime\python.exe test\echo_server.py
run.bat enable echo
run.bat run
```

macOS/Linux, using your own Python:

```
python3 src/cli.py add echo -- python3 test/echo_server.py
python3 src/cli.py enable echo
python3 src/cli.py run
```

## Not done yet

- No UI. `run.bat run` has to stay open.
- No per-call approval prompt. Enabling a server today enables all of its
  tools; the prompt, when it exists, must be a native window and not a web
  view, since a web view renders content the agent can influence.
- No auto-update, no installer, no signing.
- Not started on login.
- macOS/Linux have no bootstrap script of their own yet (they don't need
  one to run the app, but there's no equivalent of `mac/tray.py`'s
  auto-managed private interpreter from FreeClaw's own installer).
