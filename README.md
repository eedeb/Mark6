# Mark 6

The desktop half of [Mark 6](https://freeclaw.eedeb.dev/host). It runs MCP
servers on your own computer and lends their tools to your hosted agent, for
as long as it is running.

This is the terminal build. There is no window yet — but the window, when it
comes, goes on top of exactly this, because this is the part that does the
work.

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

## Install

Node 20 or newer. No dependencies.

```
git clone <this repo> && cd Mark6
node src/cli.js login
```

## Use

```
mark6 login                  pair this computer with your Mark 6 account
mark6 add <name> -- <cmd>    add a local MCP server (starts switched off)
mark6 test <name>            start it locally and list its tools
mark6 enable <name>          let your agent use it
mark6 run                    connect, and stay connected
mark6 list                   what is configured, and what is on
```

A first run, end to end:

```
$ mark6 login

  Open this page while signed in to your Mark 6 account:
      https://freeclaw.eedeb.dev/host/device

  and enter this code:
      74C3-BZUK

  Waiting for you to approve it...

Paired as "Work laptop" on account eedeb.

$ mark6 add files -- npx -y @modelcontextprotocol/server-filesystem ~/notes
Added 'files': npx -y @modelcontextprotocol/server-filesystem /home/me/notes
It is OFF. Switch it on with:  mark6 enable files

$ mark6 test files
It works. 11 tools:
  read_file                    Read the complete contents of a file...
  ...

$ mark6 enable files
'files' is now ON.

$ mark6 run
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

**Everything starts switched off.** `mark6 add` records a server; it does not
turn it on. Only the servers you `enable` are ever published, and only while
`mark6 run` is in the foreground.

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

`src/mcp/client.js` speaks the stdio MCP transport — newline-delimited
JSON-RPC over a child process's stdin and stdout. Children are spawned with
`shell: false`, and on Windows the `.cmd`/`.exe` extension is resolved by hand
so that `npx` works without a shell ever seeing your arguments.

`src/mcp/pool.js` holds the enabled servers and flattens their tools into one
list, namespaced `<server>_<tool>` so two servers cannot collide. Each
description is prefixed with where it runs, so the model — and you, reading the
transcript — can tell a tool on your laptop from one on the internet.

`src/relay.js` is the loop: publish the tool list, hold a long-poll open, run
whatever comes back, post the result. A dropped connection backs off and
retries; being unpaired is the one error it stops for.

On the server side this arrives as an ordinary HTTP MCP server in the account's
list, so FreeClaw needed no new code to use it at all — see
`FreeBusiness/relay/server.py`.

## Testing without a real MCP server

`test/echo-server.js` is a minimal one with four tools: one that works, one
that reports the machine it is on, one that always fails, and one that sleeps,
for exercising the error and timeout paths.

```
mark6 add echo -- node test/echo-server.js
mark6 enable echo
mark6 run
```

## Not done yet

- No UI. `mark6 run` has to stay open.
- No per-call approval prompt. Enabling a server today enables all of its
  tools; the prompt, when it exists, must be a native window and not a web
  view, since a web view renders content the agent can influence.
- No auto-update, no installer, no signing.
- Not started on login.
