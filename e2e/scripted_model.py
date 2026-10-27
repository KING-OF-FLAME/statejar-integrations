"""A scripted stand-in for a model endpoint, for end-to-end runs of a real client against a real StateJar where no
model is available. It speaks the three wire formats the clients use: OpenAI Responses (Codex, POST /v1/responses),
Anthropic Messages (Claude Code, POST /v1/messages) and OpenAI Chat Completions (OpenCode, POST /v1/chat/completions). It is a test harness, not a model: it never invents an answer. It answers
"OK" to a plain turn and, when the client offers a StateJar `search` tool and the prompt asks a question, it calls
that tool once and repeats the tool's output verbatim, so what reaches the user is exactly what StateJar returned.

Every request body is appended to --log (JSON lines), so a run can show what the client sent the model: the memory
the client's hooks added as context, and the tools it offered.

With --skill <SKILL.md> (Anthropic Messages only) it follows an agent skill instead, and asserts on each request that
the client handed it the skill: the skill's name and description on one line of the model's input, and the Skill tool.
Offered, it calls the Skill tool; once the skill's instructions are in the conversation it does what they say: with
StateJar's MCP tools, `remember` the user's words, or `search` with the user's question and repeat the result. What
it saw and did on each request goes to --skill-log.

    python3 scripted_model.py --port 8799 --log requests.jsonl [--skill skills/statejar/SKILL.md --skill-log s.jsonl]
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()
SKILL: dict | None = None          # set by --skill: see load_skill()


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
        elif ("statejar" in name.lower() and name.lower().endswith("search")) or name == "memory_search":
            return None, name                        # OpenClaw's memory-slot owner names it memory_search
    return None


def anthropic_text(content: object) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def anthropic_events(block: dict, stop: str) -> bytes:
    start = {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                                    "model": "scripted", "content": [], "stop_reason": None,
                                                    "stop_sequence": None,
                                                    "usage": {"input_tokens": 1, "output_tokens": 1}}}
    if block["type"] == "text":
        first = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": block["text"]}
    else:
        first = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
    events = [("message_start", start),
              ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": first}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 1}}),
              ("message_stop", {"type": "message_stop"})]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def anthropic_reply(body: dict) -> tuple[dict, str]:
    """The turn for a Messages request, read from the whole conversation (a client may add its own messages after a
    tool result): once StateJar's search has answered, repeat its output; for a question, call search; else "OK".
    Claude Code defers MCP tools behind its ToolSearch tool, so the search tool is first loaded with
    `select:<name>`, as a model would."""
    messages = body.get("messages") or []

    def blocks(role: str, kind: str) -> list[dict]:
        return [b for m in messages if m.get("role") == role and isinstance(m.get("content"), list)
                for b in m["content"] if isinstance(b, dict) and b.get("type") == kind]

    names = {b.get("id"): b.get("name", "") for b in blocks("assistant", "tool_use")}
    answered = [b for b in blocks("user", "tool_result") if names.get(b.get("tool_use_id"), "").endswith("__search")]
    if answered:
        out = answered[-1].get("content")
        return {"type": "text", "text": out if isinstance(out, str) else anthropic_text(out)}, "end_turn"
    question = ""
    for m in messages:
        if m.get("role") == "user":
            lines = [ln for ln in anthropic_text(m.get("content")).splitlines()
                     if ln.rstrip().endswith("?") and "<" not in ln]
            question = lines[-1].strip() if lines else question
    if not question:
        return {"type": "text", "text": "OK"}, "end_turn"
    tool = search_tool(body.get("tools") or [])
    if tool:
        return {"type": "tool_use", "id": "toolu_2", "name": tool[1],
                "input": {"query": question, "session": "claude-code"}}, "tool_use"
    deferred = re.search(r"mcp__[A-Za-z0-9_]*statejar[A-Za-z0-9_]*__search", json.dumps(body))
    loaded = any(n == "ToolSearch" for n in names.values())
    if deferred and not loaded and any(t.get("name") == "ToolSearch" for t in body.get("tools") or []):
        return {"type": "tool_use", "id": "toolu_1", "name": "ToolSearch",
                "input": {"query": f"select:{deferred.group(0)}", "max_results": 1}}, "tool_use"
    return {"type": "text", "text": "OK"}, "end_turn"


# --- --skill: the client must hand the model an agent skill, and the model follows it -------------------------------

def load_skill(path: str | Path) -> dict:
    """What the client must offer (the frontmatter's name and description) and the first line of the skill's body,
    whose presence in the conversation shows the client loaded the instructions when the skill was invoked."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        raise SystemExit(f"{path}: no frontmatter")
    fields = dict(re.findall(r"^(name|description): *(.+?) *$", m.group(1), re.M))
    lines = [ln.strip() for ln in m.group(2).splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return {"name": fields["name"], "description": fields["description"], "marker": lines[0]}


def model_input(body: dict) -> list[str]:
    """Every text the model is given: the system prompt, the messages' text and tool results, the tool descriptions."""
    system = body.get("system")
    out = [system] if isinstance(system, str) else [b.get("text", "") for b in system or [] if isinstance(b, dict)]
    for m in body.get("messages") or []:
        content = m.get("content")
        for b in [{"type": "text", "text": content}] if isinstance(content, str) else content or []:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else anthropic_text(c))
    return out + [t.get("description", "") for t in body.get("tools") or [] if isinstance(t, dict)]


def skill_offered(body: dict, skill: dict) -> dict:
    """The assertion: the skill's name and its whole description on one line of the model's input (a skill listing),
    and the Skill tool among the tools."""
    line = re.compile(rf"(?m)^.*(?<![\w-]){re.escape(skill['name'])}(?![\w-]).*{re.escape(skill['description'])}")
    return {"listed": any(line.search(t) for t in model_input(body)),
            "skill_tool": any(t.get("name") == "Skill" for t in body.get("tools") or [] if isinstance(t, dict))}


def user_words(messages: list[dict]) -> str:
    """What the user typed: the first user message's text, without the client's own <system-reminder> blocks."""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content")
            parts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or []
                                                                if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p.strip() for p in parts if p.strip() and not p.lstrip().startswith("<")).strip()
    return ""


def skill_reply(body: dict, skill: dict) -> tuple[dict, str, dict]:
    """One turn of a model following the skill, and what it saw. In order: (1) the skill offered, so call the Skill
    tool; (2) the skill's instructions in the conversation, so do what they say: with StateJar's MCP tools (loaded
    through ToolSearch when the client defers them), `remember` what the user says about themselves, or `search`
    with the user's question; (3) repeat the tool's output verbatim. Anything missing ends the turn with a text that
    says what was missing, so a run fails visibly instead of passing by accident. Without StateJar's MCP tools it
    takes the skill's second way: the `statejar` command its table gives for this (`statejar add "<the user's
    words>"`, `statejar search "<the user's question>"`), run with the client's shell tool."""
    messages = body.get("messages") or []

    def text(t: str) -> tuple[dict, str, dict]:
        return {"type": "text", "text": t}, "end_turn", seen

    def use(tool_id: str, name: str, args: dict) -> tuple[dict, str, dict]:
        return {"type": "tool_use", "id": tool_id, "name": name, "input": args}, "tool_use", seen

    calls = [b for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), list)
             for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
    results = {b.get("tool_use_id"): b.get("content") for m in messages
               if m.get("role") == "user" and isinstance(m.get("content"), list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"}
    words = user_words(messages)
    want = "search" if words.rstrip().endswith("?") else "remember"
    seen = {**skill_offered(body, skill), "invoked": any(c.get("name") == "Skill" for c in calls),
            "instructions": any(skill["marker"] in t for t in model_input(body)), "want": want}
    if not seen["invoked"]:
        if not (seen["listed"] and seen["skill_tool"]):
            return text(f"SKILL NOT OFFERED (listed: {seen['listed']}, Skill tool: {seen['skill_tool']})")
        return use("toolu_skill", "Skill", {"skill": skill["name"]})
    if not seen["instructions"]:
        return text("SKILL NOT LOADED: its instructions never reached the model")
    pattern = re.compile(rf"mcp__[A-Za-z0-9_-]*statejar[A-Za-z0-9_-]*__{want}")
    done = [results.get(c.get("id")) for c in calls if pattern.fullmatch(c.get("name", ""))]
    if done and done[-1] is not None:
        out = done[-1]
        return text(out if isinstance(out, str) else anthropic_text(out))
    tool = next((t["name"] for t in body.get("tools") or [] if pattern.fullmatch(t.get("name", ""))), None)
    if tool:
        return use(f"toolu_{want}", tool, {"query": words} if want == "search" else {"text": words})
    deferred = pattern.search(json.dumps(body))
    loading = any(c.get("name") == "ToolSearch" for c in calls)
    if deferred and not loading and any(t.get("name") == "ToolSearch" for t in body.get("tools") or []):
        return use(f"toolu_load_{want}", "ToolSearch", {"query": f"select:{deferred.group(0)}", "max_results": 1})
    verb = "search" if want == "search" else "add"            # no StateJar MCP tools: the skill's `statejar` command
    ran = [results.get(c.get("id")) for c in calls if c.get("id") == f"toolu_cli_{verb}"]
    if ran and ran[-1] is not None:
        return text(ran[-1] if isinstance(ran[-1], str) else anthropic_text(ran[-1]))
    listed_command = any(re.search(rf'`statejar {verb} "<[^>`]+>"`', t) for t in model_input(body))
    if listed_command and any(t.get("name") == "Bash" for t in body.get("tools") or []):
        seen["command"] = f"statejar {verb} {shlex.quote(words)}"
        return use(f"toolu_cli_{verb}", "Bash", {"command": seen["command"],
                                                 "description": f"StateJar: {verb}, as the skill says"})
    return text(f"NO STATEJAR {want.upper()} TOOL OR COMMAND")


def chat_reply(body: dict) -> dict:
    """The turn for a Chat Completions request (OpenCode): the same script in that format."""
    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        content = last.get("content")
        return {"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content)}
    tools = [{"name": (t.get("function") or {}).get("name", "")} for t in body.get("tools") or []]
    tool = search_tool(tools)
    if tool is None and any(t["name"] == "tool_call" for t in tools) and "memory_search" in json.dumps(body):
        tool = ("tool_call", "memory_search")       # OpenClaw's compact surface: tool_call(id=<tool>, args=...)
    # the user's own words: `opencode run` wraps them in quotes, Hermes appends recalled memory after them
    lines = [ln.strip().strip('"').strip() for ln in (text_of(last) if last.get("role") == "user" else "").splitlines()]
    asks = [ln for ln in lines if ln.endswith("?") and "<" not in ln]
    question = asks[0] if asks else ""
    if tool and question:
        args = {"query": question.strip(), "session": "opencode"}
        name, arguments = (("tool_call", {"id": tool[1], "args": {"query": question.strip()}}) if tool[0] == "tool_call"
                           else (tool[1], args))
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(arguments)}}]}
    return {"role": "assistant", "content": "OK"}


def chat_events(reply: dict) -> bytes:
    base = {"id": "chatcmpl_1", "object": "chat.completion.chunk", "created": 0, "model": "scripted"}
    if reply.get("tool_calls"):
        call = reply["tool_calls"][0]
        delta = {"role": "assistant", "tool_calls": [{"index": 0, **call}]}
        finish = "tool_calls"
    else:
        delta = {"role": "assistant", "content": reply["content"]}
        finish = "stop"
    chunks = [{**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
              {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}]
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


class Handler(BaseHTTPRequestHandler):
    log_path = "requests.jsonl"
    skill_log_path = "skill.jsonl"

    def reply(self, payload: bytes, kind: str = "text/event-stream") -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with LOCK, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": self.path, "body": body}) + "\n")
        path = self.path.split("?")[0]
        if path.endswith("/messages/count_tokens"):
            return self.reply(json.dumps({"input_tokens": 1}).encode(), "application/json")
        if path.endswith("/messages"):
            if SKILL is not None:
                block, stop, seen = skill_reply(body, SKILL)
                with LOCK, open(self.skill_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({**seen, "reply": block.get("name") or block.get("text", "")[:200]}) + "\n")
            else:
                block, stop = anthropic_reply(body)
            if body.get("stream"):
                return self.reply(anthropic_events(block, stop))
            whole = {"id": "msg_1", "type": "message", "role": "assistant", "model": "scripted", "content": [block],
                     "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            return self.reply(json.dumps(whole).encode(), "application/json")
        if path.endswith("/chat/completions"):
            reply = chat_reply(body)
            if body.get("stream"):
                return self.reply(chat_events(reply))
            done = {"id": "chatcmpl_1", "object": "chat.completion", "created": 0, "model": "scripted",
                    "choices": [{"index": 0, "message": reply,
                                 "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            return self.reply(json.dumps(done).encode(), "application/json")
        items = body.get("input") or []                      # OpenAI Responses (Codex)
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
        self.reply(sse(events))

    def do_GET(self) -> None:  # noqa: N802 - a models list, so a client that asks for one gets an empty catalog
        self.reply(json.dumps({"object": "list", "data": [], "models": []}).encode(), "application/json")

    def log_message(self, *args: object) -> None:
        pass


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--log", default="requests.jsonl")
    p.add_argument("--skill", help="follow this agent skill (SKILL.md) instead of the plain script")
    p.add_argument("--skill-log", default="skill.jsonl", help="what the skill script saw and did, per request")
    a = p.parse_args()
    Handler.log_path = a.log
    if a.skill:
        global SKILL
        SKILL = load_skill(a.skill)
        Handler.skill_log_path = a.skill_log
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
