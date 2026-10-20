"""What OpenClaw itself loads from this plugin, with no model and no StateJar: link-install openclaw/ into an
isolated state directory, run `openclaw statejar init`, then read `openclaw plugins inspect statejar --runtime --json`.
CI runs this on every change against the OpenClaw version it pins (Node 24.16+ on PATH).

    python3 e2e/openclaw_discovery.py --openclaw "$(command -v openclaw)"
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
TOOLS = ["memory_get", "memory_search", "statejar_forget", "statejar_remember"]


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--openclaw", default=shutil.which("openclaw") or "openclaw")
    binary = a.parse_args().openclaw
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "HOME": tmp, "OPENCLAW_STATE_DIR": str(Path(tmp) / ".openclaw")}
        env.pop("STATEJAR_API_KEY", None)

        def run(*args: str) -> str:
            r = subprocess.run([binary, *args], env=env, cwd=tmp, capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                raise SystemExit(f"openclaw {' '.join(args)} exited {r.returncode}: {r.stderr[-600:]}")
            return r.stdout

        plugin = Path(tmp) / "plugin"
        shutil.copytree(REPO / "openclaw", plugin)
        run("plugins", "install", "--link", str(plugin), "--force", "--accept-capabilities")
        run("statejar", "init", "--scope", "ci")
        config = json.loads(run("config", "get", "plugins"))
        entry = config.get("entries", {}).get("statejar", {})
        if config.get("slots", {}).get("memory") != "statejar":
            failures.append(f"memory slot: {config.get('slots')}")
        if not entry.get("hooks", {}).get("allowConversationAccess") or entry.get("config", {}).get("scope") != "ci":
            failures.append(f"init did not set the entry: {entry}")
        raw = run("plugins", "inspect", "statejar", "--runtime", "--json")
        data = json.loads(raw[raw.find("{"):])
        text = json.dumps(data)
        if '"status": "loaded"' not in text or '"kind": "memory"' not in text:
            failures.append("the plugin is not loaded as a memory plugin")
        for tool in TOOLS:
            if f'"{tool}"' not in text:
                failures.append(f"tool {tool} missing")
        if "blocked because non-bundled plugins must set" in text:
            failures.append("hooks still blocked after init")
        print(f"memory slot: {config.get('slots', {}).get('memory')}; conversation access: "
              f"{entry.get('hooks', {}).get('allowConversationAccess')}; tools: {', '.join(t for t in TOOLS if t in text)}")
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
