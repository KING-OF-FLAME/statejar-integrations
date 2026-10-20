# openclaw-statejar 0.1.0

`openclaw-statejar`, the StateJar **memory-slot plugin for OpenClaw**. It owns the slot's `memory_search` and
`memory_get` and adds `statejar_remember` and `statejar_forget`. autoRecall puts the memory a turn needs in front of
the model, and autoCapture sends the user's own messages in batches, secrets removed.

```bash
openclaw plugins install openclaw-statejar
openclaw statejar init       # selects the memory slot, turns on autoRecall and autoCapture
```

**End-to-end test (2026-10-20):** the real OpenClaw 2026.10.5 (Node 24) against a local StateJar server, with a
scripted stand-in model and two `openclaw agent --local` sessions. Facts were stored in session 1. autoRecall put
the memory in front of the model in session 2, and `memory_search` returned it. Every recall was in the audit trail,
and the throwaway account was deleted. 5 of 5 checks passed. CI installs the plugin and checks it loads as the
memory slot on every push.

**Where it works today.** Recall and capture call StateJar's REST API at `https://api.statejar.com`, which is live. The
StateJar tools that go through the hosted MCP server (`https://statejar.com/mcp`) connect once that server goes live
with StateJar's next release.

**What leaves your machine.** The current message (its first 2,000 characters) as a recall query, and your own
messages in batches for capture. Assistant replies and tool output are never sent, and API keys, cloud keys, tokens,
private keys and passwords are removed on your machine first. StateJar stores typed facts, not transcripts.
Privacy policy: https://statejar.com/privacy.

Licence: MIT.
