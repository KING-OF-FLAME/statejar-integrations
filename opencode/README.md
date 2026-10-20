# StateJar memory for OpenCode

## What it does

- **Recall.** Before the model answers, OpenCode gets the memory your message needs, as part of its system prompt.
  StateJar sends the whole state while it is small, and only the fields a question needs once it is large. Every
  recall names the handle it came from (`shm_…`, the SHA-256 of the canonical state) and is written to your audit
  trail.
- **Capture.** Your messages are queued on your machine with secrets removed (API keys and tokens, private keys,
  passwords, card numbers). They are sent in batches: after a few messages, at compaction and when OpenCode exits.
  StateJar extracts typed facts from them (name, city, budget, preferences, plans), refuses a value that fails its
  type, and keeps a fact said once in a weak form until it is said again. It stores no transcript.
- **Tools.** OpenCode gets StateJar's MCP tools (`statejar_search`, `statejar_remember`, `statejar_get`,
  `statejar_list`, `statejar_update`, `statejar_delete`, `statejar_history`, `statejar_audit`, `statejar_verify`,
  `statejar_export`) from the hosted server at `https://statejar.com/mcp`, and the plugin's own `statejar_status`,
  `statejar_pause` and `statejar_resume`.

## Install

Requires OpenCode 1.19 or later. Add the plugin and the MCP server to `opencode.json` (in your project, or
`~/.config/opencode/opencode.json` for every project):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-statejar"],
  "mcp": {
    "statejar": {
      "type": "remote",
      "url": "https://statejar.com/mcp",
      "oauth": false,
      "headers": { "Authorization": "Bearer {env:STATEJAR_API_KEY}" }
    }
  }
}
```

OpenCode installs the plugin from npm at its next start. `{env:STATEJAR_API_KEY}` keeps the key out of the file.

**Tools only, without the plugin:** keep the `mcp` block and leave out `plugin`.

## Configure

Make a developer key at https://statejar.com/api-keys and put it in the environment OpenCode runs in:

```bash
export STATEJAR_API_KEY="sj_live_…"        # macOS, Linux
```

```powershell
setx STATEJAR_API_KEY "sj_live_…"          # Windows: a new terminal picks it up
```

The other options are variables, or the plugin's options in `opencode.json`
(`"plugin": [["opencode-statejar", { "scope": "my-project" }]]`):

| variable | option | default | what it does |
|---|---|---|---|
| `STATEJAR_SCOPE` | `scope` | `opencode` | The scope (session) in your account this plugin reads and writes. A new scope builds on your newest memory. |
| `STATEJAR_AUTO_CAPTURE` | `autoCapture` | `true` | Queue your messages and send them in batches. |
| `STATEJAR_RECALL_EACH_PROMPT` | `recallEachPrompt` | `true` | Add the memory each message needs (one audited query per message). |
| `STATEJAR_API_URL` | `apiUrl` | `https://api.statejar.com/api/v1` | The REST API the plugin calls. |

If `STATEJAR_API_KEY` is empty, the plugin uses the key `statejar login` (the `statejar` package on PyPI) keeps; the
MCP tools still need the variable.

## Verify

1. `opencode mcp list` shows `statejar` connected.
2. In a session, say: "I'm Kavya, I live in Bengaluru and my laptop budget is 80000 rupees." Exit OpenCode.
3. Start OpenCode again and ask "what is my laptop budget?". The answer uses the budget and names the handle it came
   from.
4. Ask for StateJar's status: the `statejar_status` tool shows the scope, what is queued, and the last batch's status
   (202 when accepted).

## Troubleshoot

- **"no developer key yet"** in the model's context: `STATEJAR_API_KEY` is not set where OpenCode runs, and there is
  no `statejar login` key.
- **401 from the tools:** the key is wrong or revoked. Make a new one and set the variable again.
- **OpenCode asks to sign in to the MCP server:** `"oauth": false` is missing from the `statejar` entry.
- **`429` in `statejar_status`:** your plan's per-minute or monthly limit; the batch stays queued and is sent later.
- **Nothing is captured:** `statejar_status` says whether memory is paused; messages of under three words and
  commands are not captured.

## Privacy

- **Leaves your machine:** a recall sends the question (your message) to StateJar's query endpoint, and a batch sends
  your queued messages with secrets already removed. Nothing goes anywhere else.
- **Stays on your machine:** the queue lives in `statejar/clients/opencode` beside `statejar login`'s credentials
  file (`~/.config` on macOS and Linux, `%APPDATA%` on Windows), mode 600. Its `state.json` holds only counts, times
  and the last status code.
- **What StateJar stores:** typed facts, never your messages. Every disclosure is in your audit trail
  (https://statejar.com/audit). Deleting your account deletes all of it.
- **What it never touches:** the model's replies, your code and your files.
- `statejar_pause` stops recall and capture until `statejar_resume`.
