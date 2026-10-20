# StateJar memory for OpenClaw

## What it does

StateJar takes OpenClaw's memory slot (`plugins.slots.memory`).

- **autoRecall.** Before each turn, the agent gets the memory the message needs. StateJar sends the whole state while
  it is small, and only the fields a question needs once it is large. Every recall names the handle it came from
  (`shm_…`, the SHA-256 of the canonical state) and is written to your audit trail.
- **autoCapture.** After each turn, your message (never the agent's reply or tool output) is queued on your machine
  with secrets removed (API keys and tokens, private keys, passwords, card numbers). Inside the Gateway the queue is
  sent in batches: three messages, or at session end, compaction, reset and Gateway stop. A one-shot
  `openclaw agent` run sends it at the end of its turn. StateJar extracts typed facts (name, city, budget, preferences,
  plans), refuses a value that fails its type, and keeps a fact said once in a weak form until it is said again. It
  stores no transcript.
- **Tools.** `memory_search` and `memory_get` (the memory slot's tools, answered from StateJar), `statejar_remember`,
  `statejar_forget`.
- **Commands.** `openclaw statejar init | status | search | add | list | forget | export | pause | resume`.

## Install

Requires OpenClaw 2026.10.5 or later (Node 24.16+).

```bash
openclaw plugins install openclaw-statejar
openclaw statejar init
```

`init` selects StateJar for the memory slot, turns on autoRecall and autoCapture, and grants the plugin's hooks
conversation access (OpenClaw requires that for any plugin that reads a turn). It never writes your key to OpenClaw's
config.

## Configure

Make a developer key at https://statejar.com/api-keys and put it in the environment the Gateway runs in:

```bash
export STATEJAR_API_KEY="sj_live_…"        # macOS, Linux
```

```powershell
setx STATEJAR_API_KEY "sj_live_…"          # Windows: a new terminal picks it up
```

The other options live in `plugins.entries.statejar.config` (`openclaw config set …`):

| option | default | what it does |
|---|---|---|
| `scope` | `openclaw` | The scope (session) in your account this plugin reads and writes. A new scope builds on your newest memory. |
| `autoRecall` | `true` | Add the memory each turn needs (one audited query per turn). |
| `autoCapture` | `true` | Queue your messages and send them in batches. |
| `apiUrl` | `https://api.statejar.com/api/v1` | The REST API the plugin calls. |

If `STATEJAR_API_KEY` is empty, the plugin uses the key `statejar login` (the `statejar` package on PyPI) keeps.

## Verify

1. `openclaw plugins inspect statejar --runtime --json` shows it loaded, `kind: memory`, with its four tools.
2. `openclaw statejar status` shows the scope, what is queued, and the last batch's status (202 when accepted).
3. Tell your agent: "I'm Kavya, I live in Bengaluru and my laptop budget is 80000 rupees."
4. In a new session, ask "what is my laptop budget?". The answer uses the budget, and `openclaw statejar search what
   is my budget` prints the memory with its handle.

## Troubleshoot

- **Diagnostics say `before_prompt_build` is blocked:** run `openclaw statejar init`; it grants conversation access.
- **"plugin tool name conflict (memory-core)" in the log:** OpenClaw keeps memory-core loaded as a sidecar for
  dreaming; StateJar's `memory_search` and `memory_get` are the ones the agent uses (`plugins inspect` lists them under
  `statejar`).
- **Nothing is recalled:** `STATEJAR_API_KEY` is not set where the Gateway runs (`openclaw statejar status` says
  `not set`).
- **`429` in `openclaw statejar status`:** your plan's per-minute or monthly limit; the batch stays queued and is sent
  later.

## Privacy

- **Leaves your machine:** a recall sends the question (your message) to StateJar's query endpoint, and a batch sends
  your queued messages with secrets already removed. The agent's replies and tool output are never sent.
- **Stays on your machine:** the queue lives in `statejar/clients/openclaw` beside `statejar login`'s credentials
  file (`~/.config` on macOS and Linux, `%APPDATA%` on Windows), mode 600. Its `state.json` holds only counts, times
  and the last status code.
- **What StateJar stores:** typed facts, never your messages. Every disclosure is in your audit trail
  (https://statejar.com/audit). Deleting your account deletes all of it.
- `openclaw statejar pause` stops recall and capture until `openclaw statejar resume`.
