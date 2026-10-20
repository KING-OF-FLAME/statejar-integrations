# StateJar integrations

Client code that connects AI coding agents and assistants to [StateJar](https://statejar.com), the memory layer that
stores facts instead of transcripts:

- **one value per field**, checked by its type: a value that fails is refused, with a reason;
- **append-only versions**: every change is a new version, and the old value stays in history;
- **minimal-disclosure recall**: an assistant gets only what the question needs;
- **an audit trail** of every disclosure;
- **a handle per state** (`shm_…`, the SHA-256 of the canonical state), so any copy of the memory can be checked.

| Surface | Where | Status |
|---|---|---|
| Hosted MCP server (Cursor, Windsurf, VS Code, Claude Desktop, any MCP client) | `https://statejar.com/mcp` | see https://statejar.com/docs/mcp |
| Claude Code plugin | [`claude-code/`](claude-code/) | marketplace: this repository |

Every integration uses a StateJar developer key (https://statejar.com/api-keys), scoped to your account. None of
them stores a transcript. Each removes secrets before anything is sent.

## Claude Code

```bash
claude plugin marketplace add KING-OF-FLAME/statejar-integrations
claude plugin install statejar@statejar
```

See [claude-code/README.md](claude-code/README.md).

## How StateJar compares with Mem0, measured

On StateJar's memory-QA set (100 conversations, 300 questions), with **the same small local model for both systems**
(Qwen3-4B-Instruct-2507 Q4, on one GPU), StateJar's memory held the answer for **248 of 300** questions and Mem0's
(2.2.1, as shipped) for **152 of 300** (McNemar p = 1.4e-18). Answers written by one prompt from each system's memory
were right **218 of 300** times for StateJar and **159 of 300** for Mem0. This is one benchmark, in that setting only.
The method and the caveats are at https://statejar.com/docs/vs-mem0.

## Tests

```bash
python -m pytest tests/
claude plugin validate ./claude-code && claude plugin validate .
```

## Licence

StateJar SDK License (see [LICENSE](LICENSE)): use with the StateJar service, and redistribute unmodified copies.
