"""The Claude Code plugin's hook script against a stand-in StateJar API (stdlib only, no network): what it sends,
when, and what it keeps on the machine. The real end-to-end run (a fact stored in one session and recalled in a
fresh one, against a running StateJar) is in the README's "Verify" section."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "claude-code" / "scripts" / "statejar_hook.py"
TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789AB"     # built, so no credential-shaped literal is in the file
KEY = "sj_live_" + "test-key-for-the-stand-in-api-0001"


class API(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, dict, dict]] = []
    ingest_status = 202
    memory = ""

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self) -> None:  # noqa: N802
        body = self._body()
        API.calls.append(("POST", self.path, body, {k.lower(): v for k, v in self.headers.items()}))
        if self.headers.get("X-API-Key") != KEY:
            self.send_response(401)
            self.end_headers()
            return
        if self.path.endswith("/memory/ingest"):
            self.send_response(API.ingest_status)
            out = {"job_id": "j1", "status": "queued"}
        else:
            self.send_response(200)
            out = {"handle_used": "shm_" + "a" * 40 if API.memory else None, "memory_context": API.memory}
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())

    def log_message(self, *args: object) -> None:  # the test output stays quiet
        pass


@pytest.fixture
def api(tmp_path):
    API.calls, API.ingest_status, API.memory = [], 202, ""
    server = HTTPServer(("127.0.0.1", 0), API)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "CLAUDE_PLUGIN_OPTION_API_KEY": KEY, "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
           "CLAUDE_PLUGIN_OPTION_API_URL": f"http://127.0.0.1:{server.server_port}/api/v1",
           "CLAUDE_PLUGIN_OPTION_SCOPE": "cc-test", "STATEJAR_CONFIG_DIR": str(tmp_path / "nocreds")}
    env.pop("STATEJAR_API_KEY", None)
    yield env, tmp_path / "data"
    server.shutdown()


def run(env: dict, cmd: str, payload: dict | None = None, *extra: str) -> str:
    r = subprocess.run([sys.executable, str(HOOK), cmd, *extra], input=json.dumps(payload or {}), text=True,
                       capture_output=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def prompt(env: dict, text: str) -> str:
    return run(env, "prompt", {"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": text})


def test_prompts_are_queued_with_secrets_removed_and_sent_in_one_batch(api):
    env, data = api
    prompt(env, "I'm Kavya and I live in Bengaluru.")
    prompt(env, f"My budget is 80000 rupees and my GitHub token is {TOKEN} for later.")
    queue = (data / "queue.jsonl").read_text()
    assert TOKEN not in queue and "[secret: api key]" in queue
    assert oct((data / "queue.jsonl").stat().st_mode)[-3:] == "600"
    assert not [c for c in API.calls if c[1].endswith("/ingest")]       # nothing sent before a Stop
    run(env, "stop", {"hook_event_name": "Stop"})
    assert not [c for c in API.calls if c[1].endswith("/ingest")]       # two prompts: the batch waits for a third
    prompt(env, "I prefer email for anything official.")
    run(env, "stop", {"hook_event_name": "Stop"})
    sent = [c for c in API.calls if c[1].endswith("/memory/ingest")]
    assert len(sent) == 1                                               # one batch, three prompts
    body = sent[0][2]
    assert body["session_tag"] == "cc-test" and body["async"] is True
    assert "Kavya" in body["text"] and "email" in body["text"] and TOKEN not in body["text"]
    assert sent[0][3]["x-api-key"] == KEY
    assert not (data / "queue.jsonl").exists() or not (data / "queue.jsonl").read_text().strip()


def test_a_refused_batch_stays_queued_for_the_next_flush(api):
    env, data = api
    API.ingest_status = 429
    for i in range(3):
        prompt(env, f"Fact number {i} about my work as a nurse.")
    run(env, "stop", {"hook_event_name": "Stop"})
    assert len((data / "queue.jsonl").read_text().splitlines()) == 3
    assert json.loads((data / "state.json").read_text())["last_status"] == 429
    API.ingest_status = 202
    assert "sent 3" in run(env, "flush", None, "--force")


def test_recall_adds_context_only_when_there_is_memory(api):
    env, _ = api
    out = json.loads(run(env, "session-start", {"hook_event_name": "SessionStart", "source": "startup"}))
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "SessionStart" and 'session="cc-test"' in ctx["additionalContext"]
    assert prompt(env, "Which laptop fits my budget?") == ""            # no memory yet: nothing added
    API.memory = "Known user state:\nconstraints.budget.max: INR 80000"
    added = json.loads(prompt(env, "Which laptop fits my budget?"))["hookSpecificOutput"]
    assert added["hookEventName"] == "UserPromptSubmit" and "INR 80000" in added["additionalContext"]
    queries = [c for c in API.calls if c[1].endswith("/memory/query")]
    assert queries[-1][2] == {"query": "Which laptop fits my budget?", "session_tag": "cc-test"}


def test_pause_stops_recall_and_capture_until_resume(api):
    env, data = api
    run(env, "pause")
    before = len(API.calls)
    prompt(env, "I'm Kavya and I live in Bengaluru.")
    out = json.loads(run(env, "session-start", {"hook_event_name": "SessionStart"}))
    assert "paused" in out["hookSpecificOutput"]["additionalContext"]
    assert len(API.calls) == before and not (data / "queue.jsonl").exists()
    run(env, "resume")
    prompt(env, "I'm Kavya and I live in Bengaluru.")
    assert (data / "queue.jsonl").read_text().strip()


def test_commands_and_short_prompts_are_not_captured_and_options_are_honoured(api):
    env, data = api
    prompt(env, "/statejar:status")
    prompt(env, "thanks")
    assert not (data / "queue.jsonl").exists()
    env = {**env, "CLAUDE_PLUGIN_OPTION_AUTO_CAPTURE": "false", "CLAUDE_PLUGIN_OPTION_RECALL_EACH_PROMPT": "false"}
    before = len(API.calls)
    prompt(env, "I'm Kavya and I live in Bengaluru.")
    assert not (data / "queue.jsonl").exists() and len(API.calls) == before


def test_no_key_means_no_network_and_a_hint(api):
    env, _ = api
    env = {k: v for k, v in env.items() if k != "CLAUDE_PLUGIN_OPTION_API_KEY"}
    out = json.loads(run(env, "session-start", {"hook_event_name": "SessionStart"}))
    assert "no developer key" in out["hookSpecificOutput"]["additionalContext"]
    assert API.calls == []


def test_nothing_the_user_said_reaches_the_state_file_or_the_status(api):
    env, data = api
    for text in ("I'm Kavya and I live in Bengaluru.", "My budget is 80000.", "I prefer email for everything."):
        prompt(env, text)
    run(env, "stop", {"hook_event_name": "Stop"})
    state = (data / "state.json").read_text()
    status = run(env, "status")
    for word in ("Kavya", "Bengaluru", "80000", "email", KEY):
        assert word not in state and word not in status
    assert json.loads(status)["key"] == "…" + KEY[-4:]


def test_the_manifest_and_hooks_name_what_the_script_does():
    root = HOOK.parents[1]
    hooks = json.loads((root / "hooks" / "hooks.json").read_text())["hooks"]
    commands = {e: [h["args"][-1] for m in v for h in m["hooks"]] for e, v in hooks.items()}
    assert commands == {"SessionStart": ["session-start"], "UserPromptSubmit": ["prompt"], "Stop": ["stop"],
                        "PreCompact": ["pre-compact"], "SessionEnd": ["session-end"]}
    assert hooks["Stop"][0]["hooks"][0]["async"] is True
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["userConfig"]["api_key"]["sensitive"] is True
    mcp = json.loads((root / ".mcp.json").read_text())["mcpServers"]["statejar"]
    assert mcp["headers"]["Authorization"] == "Bearer ${user_config.api_key}" and mcp["type"] == "http"
    skills = sorted(p.parent.name for p in (root / "skills").glob("*/SKILL.md"))
    assert skills == ["forget", "pause", "remember", "resume", "search", "status"]
