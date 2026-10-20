# StateJar memory for Codex

## What it does

- **Recall.** At the start of every session, and before each prompt, Codex gets the memory that moment needs. StateJar
  sends the whole state while it is small, and only the fields a question needs once it is large. Every recall names
  the handle it came from (`shm_…`, the SHA-256 of the canonical state) and is written to your audit trail.
- **Capture.** Your prompts are queued on your machine with secrets removed (API keys and tokens, private keys,
  passwords, card numbers). They are sent in batches: after a few prompts, at compaction and at the end of a session.
  StateJar extracts typed facts from them (name, city, budget, preferences, plans), refuses a value that fails its
  type, and keeps a fact said once in a weak form until it is said again. It stores no transcript.
- **Tools.** Codex gets StateJar's MCP tools (`search`, `remember`, `get`, `list`, `update`, `delete`, `history`,
  `audit`, `verify`, `export`) from the hosted server at `https://statejar.com/mcp`.
- **Skills.** `$statejar:search`, `$statejar:remember`, `$statejar:forget`, `$statejar:status`, `$statejar:pause`,
  `$statejar:resume`.

It is the same plugin as the Claude Code one: Codex reads its own manifest (`.codex-plugin/plugin.json`) and hooks.

## Install

Requires Codex CLI 0.163 or later and Python 3.8 or later as `python3` (the hooks use the standard library only).

```bash
codex plugin marketplace add KING-OF-FLAME/statejar-integrations
codex plugin add statejar@statejar
```

Codex asks you to review a plugin's hooks before they run. Open Codex, run `/hooks`, and trust the five StateJar hooks
(SessionStart, UserPromptSubmit, Stop, PreCompact, SessionEnd). Until you do, the tools and skills work, but nothing is
recalled or captured on its own.

**Tools only, without the plugin:**

```bash
codex mcp add statejar --url https://statejar.com/mcp --bearer-token-env-var STATEJAR_API_KEY
```

which writes this to `~/.codex/config.toml`:

```toml
[mcp_servers.statejar]
url = "https://statejar.com/mcp"
bearer_token_env_var = "STATEJAR_API_KEY"
```

## Configure

Make a developer key at https://statejar.com/api-keys and put it in the environment Codex runs in:

```bash
export STATEJAR_API_KEY="sj_live_…"        # macOS, Linux
```

```powershell
setx STATEJAR_API_KEY "sj_live_…"          # Windows: a new terminal picks it up
```

Codex reads the key from that variable when it calls the tools; it never writes it to `config.toml`. The other
options are variables too:

| variable | default | what it does |
|---|---|---|
| `STATEJAR_SCOPE` | `codex` | The scope (session) in your account this plugin reads and writes. A new scope builds on your newest memory. |
| `STATEJAR_AUTO_CAPTURE` | `true` | Queue your prompts and send them in batches. |
| `STATEJAR_RECALL_EACH_PROMPT` | `true` | Add the memory each prompt needs (one audited query per prompt). |
| `STATEJAR_API_URL` | `https://api.statejar.com/api/v1` | The REST API the hooks call. |

If `STATEJAR_API_KEY` is empty, the hooks use the key `statejar login` (the `statejar` package on PyPI) keeps; the
tools still need the variable.

## Verify

1. `codex mcp list` shows `statejar  https://statejar.com/mcp  STATEJAR_API_KEY  enabled  Bearer token`.
2. In a session, say: "I'm Kavya, I live in Bengaluru and my laptop budget is 80000 rupees." End the session.
3. In a new session, ask "what is my laptop budget?" or run `$statejar:search what is my budget?`. The answer uses the
   budget and names the handle it came from.
4. `$statejar:status` shows the scope, what is queued, and the last batch's status (202 when accepted).

## Troubleshoot

- **Nothing is recalled or captured:** the hooks are not trusted yet. Run `/hooks` and trust the StateJar hooks.
- **"no developer key yet"** at session start: `STATEJAR_API_KEY` is not set where Codex runs, and there is no
  `statejar login` key.
- **401 from the tools:** the key is wrong or revoked. Make a new one and set the variable again.
- **`429` in `$statejar:status`:** your plan's per-minute or monthly limit; the batch stays queued and is sent later.
- **Python not found:** the hooks run `python3` (`python` on Windows); install Python 3.8+ or put it on `PATH`.

## Privacy

- **Leaves your machine:** a recall sends the question (your prompt) to StateJar's query endpoint, and a batch sends
  your queued prompts with secrets already removed. Nothing goes anywhere else.
- **Stays on your machine:** the queue lives in `statejar/clients/codex` beside `statejar login`'s credentials file
  (`~/.config` on macOS and Linux, `%APPDATA%` on Windows), mode 600. Its `state.json` holds only counts, times and the
  last status code.
- **What StateJar stores:** typed facts, never your messages. Every disclosure is in your audit trail
  (https://statejar.com/audit). Deleting your account deletes all of it.
- **What it never touches:** Codex's replies, your code and your files.
- `$statejar:pause` stops recall and capture until `$statejar:resume`.
