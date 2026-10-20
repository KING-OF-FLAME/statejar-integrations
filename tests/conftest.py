"""Shared by the plugin tests: a stand-in StateJar API (stdlib only, no network) and a runner for the hook script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "statejar"
HOOK = PLUGIN / "scripts" / "statejar_hook.py"
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


