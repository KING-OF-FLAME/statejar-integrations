"""End to end with the real Codex CLI: a fact stated in one Codex session is recalled in a fresh one.

It makes a throwaway StateJar account (deleted at the end), installs this repository's plugin into an isolated
CODEX_HOME (marketplace add, plugin add, the plugin's MCP URL pointed at --mcp), and runs two `codex exec` sessions:

  session 1  states facts; the plugin's hooks queue the prompt (secrets removed) and send it at the end of the session
  session 2  a fresh process asks about them; the hooks recall the memory, and the model can call StateJar's MCP tools

The model: `--model scripted` (default) is e2e/scripted_model.py, a stand-in that answers "OK" or calls StateJar's
`search` tool and repeats its output verbatim, for machines with no model; `--model ollama:<name>` runs Codex with
`--oss --local-provider ollama` on a real local model. Everything else is real: Codex, the plugin, StateJar's REST API
and its MCP server. Run it against a local or staging StateJar, never production:

    python3 e2e/codex_e2e.py --codex "$(command -v codex)" --api http://127.0.0.1:8000/api/v1 \\
        --mcp http://127.0.0.1:8000/mcp --out /tmp/codex-e2e

The account's password and key are made here and never printed. Hook trust is bypassed for these two runs only
(`--dangerously-bypass-hook-trust`); a user reviews the hooks once with /hooks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FACTS = "My name is Meera, I live in Pune and my laptop budget is 65000 rupees."
QUESTION = "What is my laptop budget?"
SCOPE = "codex"
PRODUCTION = re.compile(r"^https?://(?:www\.|api\.)?statejar\.com(?::\d+)?(?:/|$)")


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token = ""
        self.key = ""

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["X-API-Key"] = self.key
        elif self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def must(self, method: str, path: str, body: dict | None = None) -> dict:
        status, data = self.call(method, path, body)
        if status >= 300:
            raise RuntimeError(f"{method} {path} -> HTTP {status}")
        return data


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def install(codex: str, out: Path, mcp: str, env: dict) -> None:
    repo = out / "repo"
    shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules"))
    mcp_file = repo / "plugins" / "statejar" / "codex" / "mcp.json"
    config = json.loads(mcp_file.read_text())
    config["mcpServers"]["statejar"]["url"] = mcp
    mcp_file.write_text(json.dumps(config, indent=2))
    for args in (["plugin", "marketplace", "add", str(repo)], ["plugin", "add", "statejar@statejar"]):
        subprocess.run([codex, *args], env=env, cwd=out / "work", stdin=subprocess.DEVNULL, check=True,
                       capture_output=True, text=True)


def exec_session(codex: str, out: Path, env: dict, model_args: list[str], prompt: str, name: str) -> str:
    # stdin closed: `codex exec` reads a piped stdin as more of the prompt, and would wait for it
    r = subprocess.run([codex, "exec", "--dangerously-bypass-hook-trust", "--skip-git-repo-check", *model_args, prompt],
                       env=env, cwd=out / "work", stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    (out / f"{name}.stdout").write_text(r.stdout)
    (out / f"{name}.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"codex exec ({name}) exited {r.returncode}; see {out / (name + '.stderr')}")
    return r.stdout


def wait_for_version(api: Api, limit_s: float = 180.0) -> list[str]:
    """The scope's handle chain, oldest first, once it has a version (the batch is sent at the end of session 1)."""
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        versions = api.must("GET", f"/memory/versions?session_tag={SCOPE}").get("versions") or []
        if versions:
            return versions
        time.sleep(2)
    return []


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--codex", default=shutil.which("codex") or "codex")
    p.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    p.add_argument("--mcp", default="http://127.0.0.1:8000/mcp")
    p.add_argument("--model", default="scripted", help="scripted, or ollama:<model name>")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    if PRODUCTION.match(a.api) or PRODUCTION.match(a.mcp):
        raise SystemExit("codex_e2e.py runs against a local or staging StateJar, never production")
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "work").mkdir(parents=True)
    (out / "home").mkdir()

    api = Api(a.api)
    email = f"e2e-codex-{secrets.token_hex(4)}@example.com"
    password = "E2e-" + secrets.token_urlsafe(18)
    checks: list[tuple[str, bool, str]] = []
    model_proc = None
    try:
        if api.call("POST", "/auth/signup", {"email": email, "password": password})[0] >= 300:
            raise RuntimeError("signup refused")
        api.token = api.must("POST", "/auth/login", {"email": email, "password": password})["access_token"]
        key = api.must("POST", "/apikeys/generate", {"label": "codex e2e"})["api_key"]
        api.key = key

        env = {**os.environ, "CODEX_HOME": str(out / "home"), "HOME": str(out / "home"),
               "STATEJAR_API_KEY": key, "STATEJAR_API_URL": a.api, "STATEJAR_SCOPE": SCOPE,
               "XDG_CONFIG_HOME": str(out / "home" / ".config")}
        env.pop("STATEJAR_DATA_DIR", None)
        install(a.codex, out, a.mcp, env)

        log = out / "model-requests.jsonl"
        if a.model == "scripted":
            port = free_port()
            model_proc = subprocess.Popen([sys.executable, str(REPO / "e2e" / "scripted_model.py"), "--port", str(port),
                                           "--log", str(log)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
            config = out / "home" / "config.toml"
            config.write_text('model = "scripted"\nmodel_provider = "scripted"\n\n' + config.read_text() +
                              f'\n[model_providers.scripted]\nname = "scripted"\nbase_url = "http://127.0.0.1:{port}/v1"\n'
                              'wire_api = "responses"\n')
            model_args: list[str] = []
        elif a.model.startswith("ollama:"):
            model_args = ["--oss", "--local-provider", "ollama", "-m", a.model.split(":", 1)[1]]
        else:
            raise SystemExit("--model is scripted or ollama:<model name>")

        exec_session(a.codex, out, env, model_args, FACTS, "session1")
        versions = wait_for_version(api)
        handle = versions[-1] if versions else "shm_none"
        state = api.must("GET", f"/memory/state/{handle}")["state"] if versions else {}
        flat = json.dumps(state)
        stored = all(x in flat for x in ("Meera", "Pune", "65000"))
        checks.append(("session 1: the facts were captured and stored", stored and bool(versions),
                       f"{len(versions)} version(s), handle {handle[:16]}…" if versions else "no version"))

        answer = exec_session(a.codex, out, env, model_args, QUESTION, "session2")
        if a.model == "scripted":
            requests = [json.loads(x) for x in log.read_text().splitlines()]
            s2 = [r for r in requests if QUESTION in json.dumps(r["body"].get("input"))]
            first = json.dumps(s2[0]["body"].get("input")) if s2 else ""
            checks.append(("session 2: the hooks put the memory in front of the model",
                           "65000" in first and handle in first, "budget and handle in the model's input"))
            tool_round = [r for r in s2 if "function_call_output" in json.dumps(r["body"].get("input"))]
            checks.append(("session 2: Codex called StateJar's MCP search and got the memory back",
                           bool(tool_round) and "65000" in answer and handle in answer,
                           "the tool's output, repeated by the stand-in model"))
        else:
            checks.append(("session 2: the model answered with the stored budget", "65000" in answer.replace(",", ""),
                           answer.strip().splitlines()[-1][:120] if answer.strip() else "empty"))
        audit = api.must("GET", "/audit").get("entries") or []
        checks.append(("every recall is in the audit trail", len(audit) >= 2, f"{len(audit)} audit entries"))
    finally:
        if model_proc:
            model_proc.terminate()
        if api.token:
            api.key = ""
            status, _ = api.call("DELETE", "/account", {"confirm_email": email, "password": password})
            checks.append(("the throwaway account was deleted", status == 200, f"HTTP {status}"))
    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    summary = {"model": a.model, "api": a.api, "checks": [{"check": c[0], "pass": c[1], "detail": c[2]} for c in checks]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return 0 if all(c[1] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
