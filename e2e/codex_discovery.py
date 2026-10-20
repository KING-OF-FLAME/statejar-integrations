"""What Codex itself finds in this plugin, with no model and no StateJar: install the marketplace and the plugin into
an isolated CODEX_HOME, then ask Codex (`codex mcp list` and its app-server's hooks/list and skills/list) what it
loaded. CI runs this on every change, against the Codex version it pins.

    python3 e2e/codex_discovery.py --codex "$(command -v codex)"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = {"sessionStart", "userPromptSubmit", "stop", "preCompact", "sessionEnd"}
SKILLS = {f"statejar:{s}" for s in ("search", "remember", "forget", "status", "pause", "resume")}


def app_server(codex: str, env: dict, cwd: Path) -> tuple[dict, dict]:
    p = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, env=env, cwd=cwd)

    def send(i: int, method: str, params: dict) -> dict:
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
        p.stdin.flush()
        while True:
            line = p.stdout.readline()
            if not line:
                raise RuntimeError(f"codex app-server closed before answering {method}")
            msg = json.loads(line)
            if msg.get("id") == i:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    try:
        send(1, "initialize", {"clientInfo": {"name": "statejar-discovery", "version": "0.1.0"}})
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n")
        p.stdin.flush()
        return (send(2, "hooks/list", {"cwds": [str(cwd)]}),
                send(3, "skills/list", {"cwds": [str(cwd)], "forceReload": True}))
    finally:
        p.terminate()


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--codex", default=shutil.which("codex") or "codex")
    codex = a.parse_args().codex
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        home, work = Path(tmp) / "home", Path(tmp) / "work"
        home.mkdir()
        work.mkdir()
        env = {**os.environ, "CODEX_HOME": str(home), "HOME": str(home)}
        for args in (["plugin", "marketplace", "add", str(REPO)], ["plugin", "add", "statejar@statejar"]):
            subprocess.run([codex, *args], env=env, cwd=work, check=True, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        mcp = subprocess.run([codex, "mcp", "list"], env=env, cwd=work, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL).stdout
        row = next((line.split() for line in mcp.splitlines() if line.startswith("statejar ")), [])
        if row[:3] != ["statejar", "https://statejar.com/mcp", "STATEJAR_API_KEY"]:
            failures.append(f"MCP server: {row}")
        hooks, skills = app_server(codex, env, work)
        found = {h["eventName"]: h for d in hooks["data"] for h in d["hooks"] if h.get("pluginId") == "statejar@statejar"}
        if set(found) != HOOKS:
            failures.append(f"hooks: {sorted(found)}")
        if any(h["handlerType"] != "command" or "--client codex" not in h["command"] for h in found.values()):
            failures.append("a hook is not the shared script with --client codex")
        names = {s["name"] for d in skills["data"] for s in d["skills"] if s["name"].startswith("statejar:")}
        if names != SKILLS:
            failures.append(f"skills: {sorted(names)}")
        errors = [e for d in skills["data"] for e in d.get("errors", []) if "statejar" in json.dumps(e)]
        if errors:
            failures.append(f"skill errors: {errors}")
        print(f"MCP: {' '.join(row[:3])}")
        print(f"hooks: {', '.join(sorted(found))} (trust: {', '.join(sorted({h['trustStatus'] for h in found.values()}))})")
        print(f"skills: {', '.join(sorted(names))}")
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
