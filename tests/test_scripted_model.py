"""The scripted stand-in model the end-to-end runs use: it never invents an answer. For each wire format it answers
"OK" to a plain turn, calls StateJar's search tool for a question, and repeats the tool's output verbatim."""

from __future__ import annotations

import json
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "e2e"))
import scripted_model as sm  # noqa: E402

RESULT = "Known user state (retrieved via StateJar handle shm_abc):\nconstraints.budget.max: INR 65000"


def test_anthropic_loads_a_deferred_tool_then_searches_then_repeats_the_result():
    body = {"tools": [{"name": "ToolSearch"}],
            "system": "deferred tools: mcp__plugin_statejar_statejar__search",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "What is my laptop budget?"}]}]}
    block, stop = sm.anthropic_reply(body)
    assert (block["name"], stop) == ("ToolSearch", "tool_use")
    assert block["input"]["query"] == "select:mcp__plugin_statejar_statejar__search"
    body["tools"].append({"name": "mcp__plugin_statejar_statejar__search"})
    body["messages"] += [{"role": "assistant", "content": [block]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block["id"], "content": []}]}]
    block2, _ = sm.anthropic_reply(body)
    assert block2["name"] == "mcp__plugin_statejar_statejar__search"
    assert block2["input"] == {"query": "What is my laptop budget?", "session": "claude-code"}
    body["messages"] += [{"role": "assistant", "content": [block2]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block2["id"],
                                                       "content": [{"type": "text", "text": RESULT}]}]},
                         {"role": "user", "content": "<reminder>the client may add messages after a result</reminder>"}]
    final, stop = sm.anthropic_reply(body)
    assert final == {"type": "text", "text": RESULT} and stop == "end_turn"


def test_anthropic_says_ok_without_a_question():
    body = {"tools": [{"name": "mcp__plugin_statejar_statejar__search"}],
            "messages": [{"role": "user", "content": "My name is Meera and I live in Pune."}]}
    assert sm.anthropic_reply(body) == ({"type": "text", "text": "OK"}, "end_turn")


def test_responses_namespace_tool_is_found_and_called():
    tools = [{"type": "namespace", "name": "mcp__statejar", "tools": [{"type": "function", "name": "search"}]}]
    assert sm.search_tool(tools) == ("mcp__statejar", "search")
    events = sm.call(("mcp__statejar", "search"), {"query": "q?"})
    item = events[1]["item"]
    assert item["namespace"] == "mcp__statejar" and item["name"] == "search" and json.loads(item["arguments"]) == {"query": "q?"}


def test_chat_completions_calls_search_then_repeats_the_tool_message():
    body = {"tools": [{"type": "function", "function": {"name": "statejar_search"}}],
            "messages": [{"role": "user", "content": "What is my laptop budget?"}]}
    reply = sm.chat_reply(body)
    (call,) = reply["tool_calls"]
    assert call["function"]["name"] == "statejar_search"
    body["messages"] += [reply, {"role": "tool", "tool_call_id": call["id"], "content": RESULT}]
    assert sm.chat_reply(body) == {"role": "assistant", "content": RESULT}
    assert b"data: [DONE]" in sm.chat_events({"role": "assistant", "content": "OK"})


def test_chat_completions_reads_a_question_opencode_run_quoted():
    body = {"tools": [{"type": "function", "function": {"name": "statejar_search"}}],
            "messages": [{"role": "user", "content": '"What is my laptop budget?"'}]}
    assert sm.chat_reply(body)["tool_calls"][0]["function"]["name"] == "statejar_search"


def test_chat_completions_reads_the_question_before_memory_hermes_appended():
    text = "What is my laptop budget?\n\n<memory-context>\nKnown user state:\nconstraints.budget.max: INR 65000\n</memory-context>"
    body = {"tools": [{"type": "function", "function": {"name": "statejar_search"}}],
            "messages": [{"role": "user", "content": text}]}
    call = sm.chat_reply(body)["tool_calls"][0]["function"]
    assert call["name"] == "statejar_search" and json.loads(call["arguments"])["query"] == "What is my laptop budget?"
