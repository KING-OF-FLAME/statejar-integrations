# StateJar plugin for Claude Code and Codex 0.1.0

The StateJar plugin for **Claude Code** and **Codex** (one plugin, one marketplace): recall at session start and before
each prompt, batched capture with secrets removed, StateJar's MCP tools, and the `/statejar` commands (`search`,
`remember`, `forget`, `status`, `pause`, `resume`).

```bash
# Claude Code
claude plugin marketplace add KING-OF-FLAME/statejar-integrations
claude plugin install statejar@statejar
# Codex
codex plugin marketplace add KING-OF-FLAME/statejar-integrations
codex plugin add statejar@statejar
```

**End-to-end test (2026-10-20):** the real Claude Code and the real Codex 0.163.0 against a local StateJar server
running the hosted server's code, with a scripted stand-in model. Facts were stored in session 1, put in front of the
model in a fresh session 2, and returned by StateJar's MCP `search` tool. Every recall was in the audit trail, and
the throwaway account was deleted. Each client passed 5 of 5 checks. CI validates the plugin and the marketplace
(`claude plugin validate`) and installs the plugin into Codex on every push.

**Where it works today.** Recall and capture call StateJar's REST API at `https://api.statejar.com`, which is live. The
StateJar tools that go through the hosted MCP server (`https://statejar.com/mcp`) connect once that server goes live
with StateJar's next release.

**What leaves your machine.** The current message (its first 2,000 characters) as a recall query, and your own
messages in batches for capture. Assistant replies and tool output are never sent, and API keys, cloud keys, tokens,
private keys and passwords are removed on your machine first. StateJar stores typed facts, not transcripts.
Privacy policy: https://statejar.com/privacy.

Licence: MIT.
