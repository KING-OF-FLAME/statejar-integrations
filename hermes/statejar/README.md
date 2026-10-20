# StateJar memory for Hermes Agent

## What it does

- **Recall.** Before each model call, Hermes gets the memory your message needs. StateJar sends the whole state while
  it is small, and only the fields a question needs once it is large. Every recall names the handle it came from
  (`shm_…`, the SHA-256 of the canonical state) and is written to your audit trail.
- **Capture.** Your messages (never the agent's replies or tool output) are queued on your machine with secrets
  removed (API keys and tokens, private keys, passwords, card numbers), and sent in batches: three messages, or at
  compression, session end and shutdown. What Hermes's own memory tool saves is queued the same way. StateJar extracts
  typed facts from them (name, city, budget, preferences, plans), refuses a value that fails its type, and keeps a
  fact said once in a weak form until it is said again. It stores no transcript.
- **Tools.** `statejar_search`, `statejar_remember`, `statejar_forget`, `statejar_list`.
- **Commands.** `hermes statejar status | search | add | list | forget | export | pause | resume`.

Scheduled (cron), subagent and flush runs read memory but never write to it.

## Install

Requires Hermes Agent 0.19 or later. The provider uses Python's standard library only.

```bash
hermes plugins install KING-OF-FLAME/statejar-integrations/hermes/statejar --enable
hermes memory setup          # choose statejar and paste your key
```

## Configure

Make a developer key at https://statejar.com/api-keys. `hermes memory setup` stores it as `STATEJAR_API_KEY` in your
profile's `.env` and sets `memory.provider: statejar` in `config.yaml`:

```yaml
memory:
  provider: statejar
```

Other options go in `statejar.json` in your Hermes home (`~/.hermes` unless you use profiles):

| key | default | what it does |
|---|---|---|
| `scope` | `hermes` | The scope (session) in your account this provider reads and writes. A new scope builds on your newest memory. |
| `auto_capture` | `true` | Queue your messages and send them in batches. |
| `recall_each_prompt` | `true` | Recall what each message needs (one audited query per message). |
| `api_url` | `https://api.statejar.com/api/v1` | The REST API the provider calls. |

If no key is set, the provider uses the one `statejar login` (the `statejar` package on PyPI) keeps.

## Verify

1. `hermes memory status` lists `statejar ← active`.
2. `hermes statejar status` shows the scope, what is queued, and the last batch's status (202 when accepted).
3. In a chat, say: "I'm Kavya, I live in Bengaluru and my laptop budget is 80000 rupees." End the chat.
4. In a new chat, ask "what is my laptop budget?". The answer uses the budget, and `hermes statejar search what is
   my budget` prints the memory with its handle.

## Troubleshoot

- **`statejar` is not in `hermes memory status`:** the plugin is not installed in this profile; install it again
  with `hermes plugins install …`.
- **"no developer key yet"** in the agent's context: run `hermes memory setup` again, or `statejar login`.
- **HTTP 401 from a tool:** the key is wrong or revoked. Make a new one and run `hermes memory setup`.
- **`429` in `hermes statejar status`:** your plan's per-minute or monthly limit; the batch stays queued and is sent
  later.
- **Nothing is captured:** `hermes statejar status` says whether memory is paused; messages of under three words and
  slash commands are not captured.

## Privacy

- **Leaves your machine:** a recall sends the question (your message) to StateJar's query endpoint, and a batch sends
  your queued messages with secrets already removed. The agent's replies and tool output are never sent.
- **Stays on your machine:** the queue lives in `statejar/` inside your Hermes home (mode 600), per profile. Its
  `state.json` holds only counts, times and the last status code.
- **What StateJar stores:** typed facts, never your messages. Every disclosure is in your audit trail
  (https://statejar.com/audit). Deleting your account deletes all of it.
- `hermes statejar pause` stops recall and capture until `hermes statejar resume`.
