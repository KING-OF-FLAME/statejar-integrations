# StateJar memory provider for Hermes Agent 0.1.0

The StateJar **memory provider for Hermes Agent** (`memory.provider: statejar`): recall into the model's input,
capture of the user's own messages (secrets removed), and the `statejar_search` tool.

```bash
hermes plugins install KING-OF-FLAME/statejar-integrations/hermes/statejar --enable
hermes memory setup          # choose statejar
```

**End-to-end test (2026-10-20):** the real Hermes Agent 0.19.0 against a local StateJar server, with a scripted
stand-in model. Facts were stored in session 1, put in front of the model in a fresh session 2, and returned by
`statejar_search`. Every recall was in the audit trail, and the throwaway account was deleted. 5 of 5 checks passed.
CI loads the provider through Hermes's own discovery on every push.

**Where it works today.** Recall and capture call StateJar's REST API at `https://api.statejar.com`, which is live. The
StateJar tools that go through the hosted MCP server (`https://statejar.com/mcp`) connect once that server goes live
with StateJar's next release.

**What leaves your machine.** The current message (its first 2,000 characters) as a recall query, and your own
messages in batches for capture. Assistant replies and tool output are never sent, and API keys, cloud keys, tokens,
private keys and passwords are removed on your machine first. StateJar stores typed facts, not transcripts.
Privacy policy: https://statejar.com/privacy.

Licence: MIT.
