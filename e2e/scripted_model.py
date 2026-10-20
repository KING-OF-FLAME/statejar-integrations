"""A scripted stand-in for an OpenAI Responses endpoint, for end-to-end runs of a real client (Codex) against a real
StateJar where no model is available. It is a test harness, not a model: it never invents an answer. It answers
"OK" to a plain turn and, when the client offers a StateJar `search` tool and the prompt asks a question, it calls
that tool once and repeats the tool's output verbatim, so what reaches the user is exactly what StateJar returned.

Every request body is appended to --log (JSON lines), so a run can show what the client sent the model: the memory
the client's hooks added as context, and the tools it offered.

    python3 scripted_model.py --port 8799 --log requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()


def sse(events: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def message(text: str) -> list[dict]:
    item = {"type": "message", "role": "assistant", "id": "msg_1", "content": [{"type": "output_text", "text": text}]}
    return [{"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.output_item.done", "item": item},
            {"type": "response.completed", "response": {"id": "resp_1"}}]


def call(tool: tuple[str | None, str], arguments: dict) -> list[dict]:
    namespace, name = tool
    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name,
            "arguments": json.dumps(arguments)}
    if namespace:
        item["namespace"] = namespace
    return [{"type": "response.created", "response": {"id": "resp_2"}},
            {"type": "response.output_item.done", "item": item},
            {"type": "response.completed", "response": {"id": "resp_2"}}]


def text_of(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict))


def search_tool(tools: list[dict]) -> tuple[str | None, str] | None:
    """StateJar's search tool as the client offers it: (namespace, name). Codex groups an MCP server's tools in a
    namespace tool (`mcp__statejar` holding `search`); other clients flatten them (`mcp__statejar__search`)."""
    for t in tools or []:
        name = t.get("name") or (t.get("function") or {}).get("name") or ""
        if t.get("type") == "namespace" and "statejar" in name.lower():
            for sub in t.get("tools") or []:
                if sub.get("name") == "search":
                    return name, "search"
        elif "statejar" in name.lower() and name.lower().endswith("search"):
            return None, name
    return None


class Handler(BaseHTTPRequestHandler):
    log_path = "requests.jsonl"

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with LOCK, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": self.path, "body": body}) + "\n")
        items = body.get("input") or []
        outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
        users = [text_of(i) for i in items if isinstance(i, dict) and i.get("role") == "user"]
        question = users[-1] if users else ""
        tool = search_tool(body.get("tools") or [])
        if outputs:
            out = outputs[-1].get("output")
            events = message(out if isinstance(out, str) else json.dumps(out))
        elif tool and question.rstrip().endswith("?"):
            events = call(tool, {"query": question.strip(), "session": "codex"})
        else:
            events = message("OK")
        payload = sse(events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - a models list, so a client that asks for one gets an empty catalog
        payload = json.dumps({"object": "list", "data": [], "models": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--log", default="requests.jsonl")
    a = p.parse_args()
    Handler.log_path = a.log
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
