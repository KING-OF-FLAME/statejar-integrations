# Third-party listings: drafts for the owner's approval

Nothing in this folder has been submitted. The owner asked for third-party listings (ClawHub, the Hermes catalog,
MCP registries) to be prepared and listed for approval (2026-10-20). Each one below gives the text to submit, the
account it needs, the commands, and what is not verified yet.

Two things must be true before any listing goes out:

1. The hosted MCP server answers at `https://statejar.com/mcp`. It goes live with StateJar's next release.
2. This repository is public at `github.com/KING-OF-FLAME/statejar-integrations`. Every listing links to it.

## What leaves the user's machine (the same text for every listing)

- **Recall:** the user's current message (its first 2,000 characters) goes to the StateJar API as a query. The facts
  that message needs come back as context, with the handle (`shm_…`) of the state they came from.
- **Capture:** the user's own messages go to the StateJar API in batches. The assistant's replies and tool output are
  never sent. API keys, cloud keys, tokens, private keys and passwords are removed on the user's machine before
  anything is sent.
- **Where it goes:** `api.statejar.com` and `statejar.com/mcp`, on StateJar's own server (one VPS in France). Facts
  are extracted there by StateJar's own model. No third-party model reads the messages, and StateJar stores no
  transcript.
- **Credentials:** a StateJar developer key (`sj_live_…`), read from `STATEJAR_API_KEY` or from what `statejar login`
  keeps (the OS keychain, or a file only the user can read).
- Privacy policy: https://statejar.com/privacy. Delete an account and everything in it: https://statejar.com/delete-account.

## 1. The official MCP Registry (registry.modelcontextprotocol.io)

File: [`mcp-registry/server.json`](mcp-registry/server.json): the remote server `https://statejar.com/mcp`
(streamable-http, an `Authorization: Bearer sj_live_…` header).

The name decides the account:

- **`com.statejar/memory` (recommended):** proves that StateJar owns `statejar.com`. You make an Ed25519 key pair on
  your own machine (`openssl genpkey -algorithm Ed25519 -out key.pem`, OpenSSL 3), keep the private key yourself, and
  publish only the public key. Either add the DNS TXT record `v=MCPv1; k=ed25519; p=<public key>` at the apex of
  statejar.com (GoDaddy), or send me the public-key line and I host it at `https://statejar.com/.well-known/mcp-registry-auth`
  (a website-only change). Then run `mcp-publisher login dns --domain statejar.com --private-key <key>` (or `login
  http`), followed by `mcp-publisher publish` in this folder.
- **`io.github.king-of-flame/statejar`:** no domain step. Run `mcp-publisher login github` with your GitHub account
  (a device code), change `name` in server.json, then `mcp-publisher publish`.

Directories such as PulseMCP and Glama also list servers. Whether they pick up entries from the official registry,
or need their own form, is **not verified**. Check after the official entry is live.

## 2. ClawHub (OpenClaw's plugin registry)

Package: [`../openclaw`](../openclaw), `openclaw-statejar` 0.1.0. It carries the native plugin manifest
`openclaw.plugin.json` (the memory slot, autoRecall and autoCapture) and `package.json` with `openclaw.extensions`.
Commands, as OpenClaw 2026.10.5's own docs give them (`docs/plugins/manage-plugins.md`):

```bash
npm i -g clawhub
clawhub login                                   # your ClawHub account
cd openclaw && clawhub package validate
clawhub package publish <owner>/openclaw-statejar --dry-run
clawhub package publish <owner>/openclaw-statejar
```

Users then install it with `openclaw plugins install clawhub:<owner>/openclaw-statejar`. ClawHub reviews and
verifies a new release before it shows in search. The publisher owner name is yours to choose.

Listing text: **StateJar memory.** A memory-slot plugin. It recalls the facts each message needs before the prompt is
built, and captures the user's messages in batches with secrets removed. It also adds `memory_search` / `memory_get`
and `openclaw statejar init`. Typed facts, one value per field, content-addressed versions you can verify.

## 3. The Hermes Agent plugin catalog (Nous Research)

Provider: [`../hermes/statejar`](../hermes/statejar). `plugin.yaml` sets the name `statejar` and version 0.1.0, and
the code registers a memory provider (`memory.provider: statejar`). Hermes installs a repository subdirectory:
`hermes plugins install KING-OF-FLAME/statejar-integrations/hermes/statejar --enable` (the provider's README).

**Not verified:** how an entry gets into the catalog at hermes-agent.nousresearch.com/docs/plugins. I found no
submission guide from here (that site's documentation does not resolve from this sandbox). The catalog's entries
show a category (Memory) and a data disclosure. Use this text:

- Name: StateJar. Category: Memory.
- Description: Verifiable memory for Hermes Agent. Facts are typed with one value per field, every version is
  content-addressed and verifiable, recall sends only what a message needs, and an audit trail logs every disclosure.
- Data disclosure: the section "What leaves the user's machine" above.

## Not third-party (our own namespace, no approval needed)

- Claude Code and Codex: this repository is their marketplace (`/plugin marketplace add KING-OF-FLAME/statejar-integrations`).
- OpenCode: the `opencode-statejar` package, published to npm under our account.
