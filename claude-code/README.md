# StateJar memory for Claude Code

## What it does

- **Recall.** At the start of every session, and before each prompt, Claude gets the memory that moment needs. StateJar
  sends the whole state while it is small, and only the fields a question needs once it is large. Every recall names
  the handle it came from (`shm_…`, the SHA-256 of the canonical state) and is written to your audit trail.
- **Capture.** Your prompts are queued on your machine with secrets removed (API keys and tokens, private keys,
  passwords, card numbers). They are sent in batches: after a few prompts, at compaction and at the end of a session.
  StateJar extracts typed facts from them (name, city, budget, preferences, plans), refuses a value that fails its
  type, and keeps a fact said once in a weak form until it is said again. It stores no transcript.
- **Tools.** Claude gets StateJar's MCP tools (`search`, `remember`, `get`, `list`, `update`, `delete`, `history`,
  `audit`, `verify`, `export`) from the hosted server at `https://statejar.com/mcp`.
- **Commands.** `/statejar:search`, `/statejar:status`, `/statejar:remember`, `/statejar:forget`, `/statejar:pause`,
  `/statejar:resume`.

## Install

```bash
claude plugin marketplace add KING-OF-FLAME/statejar-integrations
claude plugin install statejar@statejar
```

or, inside Claude Code: `/plugin marketplace add KING-OF-FLAME/statejar-integrations`, then
`/plugin install statejar@statejar`.

Requires Python 3.8 or later as `python3` (the hooks use the standard library only).

## Configure

Make a developer key at https://statejar.com/api-keys, then run `/plugin configure statejar@statejar` in Claude
Code, or this from a shell:

```bash
echo '{"api_key": "sj_live_…"}' | claude plugin configure statejar@statejar --values-stdin
```

The key is kept in your system's secure credential store. The other options:

| option | default | what it does |
|---|---|---|
| `scope` | `claude-code` | The scope (session) in your account this plugin reads and writes. A new scope builds on your newest memory. |
| `auto_capture` | `true` | Queue your prompts and send them in batches. |
| `recall_each_prompt` | `true` | Add the memory each prompt needs (one audited query per prompt). |
| `api_url` | `https://api.statejar.com/api/v1` | The REST API the hooks call. |
| `mcp_url` | `https://statejar.com/mcp` | The MCP server Claude's tools use. |

If you already ran `statejar login` (the `statejar` package on PyPI), the hooks use that key when the option is empty.

## Verify

1. `claude mcp list` shows `plugin:statejar:statejar: https://statejar.com/mcp (HTTP) - ✓ Connected`.
2. In a session, say: "I'm Kavya, I live in Bengaluru and my laptop budget is 80000 rupees." End the session.
3. In a new session, run `/statejar:search what is my budget?` or just ask about laptops. The answer uses the budget
   and names the handle it came from.
4. `/statejar:status` shows the scope, what is queued, and the last batch's status (202 when accepted).

## Troubleshoot

- **"no developer key yet"** at session start: the `api_key` option is empty and there is no `statejar login` key.
- **`! Needs authentication` or 401 from the tools:** the key is wrong or revoked. Make a new one and configure it again.
- **`429` in `/statejar:status`:** your plan's per-minute or monthly limit; the batch stays queued and is sent later.
- **Nothing is captured:** `/statejar:status` says whether memory is paused; prompts of under three words and slash
  commands are not captured.
- **Python not found:** the hooks run `python3`; install Python 3.8+ or put it on `PATH`.

## Privacy

- **Leaves your machine:** a recall sends the question (your prompt) to StateJar's query endpoint, and a batch sends
  your queued prompts with secrets already removed. Nothing goes anywhere else.
- **Stays on your machine:** the queue lives in the plugin's data folder (mode 600). Its `state.json` holds only
  counts, times and the last status code.
- **What StateJar stores:** typed facts, never your messages. Every disclosure is in your audit trail
  (https://statejar.com/audit). Deleting your account deletes all of it.
- **What it never touches:** Claude's replies, your code and your files.
- `/statejar:pause` stops recall and capture until `/statejar:resume`.
