# opencode-statejar 0.1.0

`opencode-statejar`, the StateJar plugin for **OpenCode**: memory added to the system prompt each turn, batched
capture with secrets removed, `statejar_status`, and StateJar's MCP tools through the `mcp` block. Add
`"plugin": ["opencode-statejar"]` to `opencode.json`; see `opencode/README.md`.

**End-to-end test (2026-10-20):** the real OpenCode 1.19.3 against a local StateJar server running the hosted server's
code, with a scripted stand-in model. Facts were stored in session 1, put in the system prompt of a fresh session 2,
and returned by StateJar's MCP `search` tool. Every recall was in the audit trail, and the throwaway account was
deleted. 5 of 5 checks passed.

**Where it works today.** Recall and capture call StateJar's REST API at `https://api.statejar.com`, which is live. The
StateJar tools that go through the hosted MCP server (`https://statejar.com/mcp`) connect once that server goes live
with StateJar's next release.

**What leaves your machine.** The current message (its first 2,000 characters) as a recall query, and your own
messages in batches for capture. Assistant replies and tool output are never sent, and API keys, cloud keys, tokens,
private keys and passwords are removed on your machine first. StateJar stores typed facts, not transcripts.
Privacy policy: https://statejar.com/privacy.

Licence: MIT.
