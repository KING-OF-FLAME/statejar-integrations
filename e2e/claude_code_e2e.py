"""End to end with the real Claude Code CLI: a fact stated in one session is recalled in a fresh one.

It makes a throwaway StateJar account (deleted at the end), installs this repository's plugin into an isolated HOME
(marketplace add, plugin install, plugin configure with the key and the --api / --mcp URLs), and runs two `claude -p`
sessions:

  session 1  states facts; the plugin's hooks queue the prompt (secrets removed) and send it at the end of the session
  session 2  a fresh process asks about them; the hooks recall the memory, and the model can call StateJar's MCP tools

The model is e2e/scripted_model.py (ANTHROPIC_BASE_URL points at it): a stand-in that answers "OK" or calls
StateJar's `search` tool and repeats its output verbatim, for machines with no model. Everything else is real:
Claude Code, the plugin, StateJar's REST API and its MCP server. Run it against a local or staging StateJar, never
production:

    python3 e2e/claude_code_e2e.py --claude "$(command -v claude)" --api http://127.0.0.1:8000/api/v1 \\
        --mcp http://127.0.0.1:8000/mcp --out /tmp/claude-code-e2e

The account's password and key are made here and never printed; the key reaches Claude Code through
`claude plugin configure --values-stdin`, as a user's would.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import FACTS, QUESTION, REPO, Api, refuse_production, report, scripted_model, stored_check, throwaway_account

SCOPE = "claude-code"
SEARCH = "mcp__plugin_statejar_statejar__search"


def claude(binary: str, out: Path, env: dict, *args: str, stdin: str | None = None, name: str = "") -> str:
    r = subprocess.run([binary, *args], env=env, cwd=out / "work", input=stdin,
                       stdin=None if stdin is not None else subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=600)
    if name:
        (out / f"{name}.stdout").write_text(r.stdout)
        (out / f"{name}.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"claude {args[0]} exited {r.returncode}: {r.stderr.strip()[-300:]}")
    return r.stdout


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--claude", default=shutil.which("claude") or "claude")
    p.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    p.add_argument("--mcp", default="http://127.0.0.1:8000/mcp")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    refuse_production(a.api, a.mcp)
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "work").mkdir(parents=True)
    (out / "home").mkdir()
    api = Api(a.api)
    checks: list = []
    log = out / "model-requests.jsonl"
    with contextlib.ExitStack() as stack:
        key = stack.enter_context(throwaway_account(api, "claude-code", checks))
        port = stack.enter_context(scripted_model(log))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("ANTHROPIC_", "CLAUDE_", "STATEJAR_")) or k == "CLAUDE_CODE_GIT_BASH_PATH"}
        env.update({"HOME": str(out / "home"), "XDG_CONFIG_HOME": str(out / "home" / ".config"),
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_API_KEY": "scripted-model",
                    "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "DISABLE_AUTOUPDATER": "1"})
        repo = out / "repo"
        shutil.copytree(REPO, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules"))
        claude(a.claude, out, env, "plugin", "marketplace", "add", str(repo))
        claude(a.claude, out, env, "plugin", "install", "statejar@statejar")
        values = {"api_key": key, "api_url": a.api, "mcp_url": a.mcp, "scope": SCOPE}
        claude(a.claude, out, env, "plugin", "configure", "statejar@statejar", "--values-stdin",
               stdin=json.dumps(values))

        allow = ["--allowedTools", SEARCH]          # after the prompt: the option takes several values
        claude(a.claude, out, env, "-p", FACTS, *allow, name="session1")
        check, handle = stored_check(api, SCOPE)
        checks.append(check)
        answer = claude(a.claude, out, env, "-p", QUESTION, *allow, name="session2")
        requests = [json.loads(x) for x in log.read_text().splitlines()]
        s2 = [r for r in requests if r["path"].split("?")[0].endswith("/messages")
              and QUESTION in json.dumps(r["body"].get("messages"))]
        first = json.dumps(s2[0]["body"]) if s2 else ""
        checks.append(("session 2: the hooks put the memory in front of the model",
                       "65000" in first and handle in first, "budget and handle in the model's input"))
        tool_round = [r for r in s2 if '"tool_result"' in json.dumps(r["body"].get("messages"))]
        checks.append(("session 2: Claude Code called StateJar's MCP search and got the memory back",
                       bool(tool_round) and "65000" in answer and handle in answer,
                       "the tool's output, repeated by the stand-in model"))
        audit = api.must("GET", "/audit").get("entries") or []
        checks.append(("every recall is in the audit trail", len(audit) >= 2, f"{len(audit)} audit entries"))
    return report(checks, out, {"client": "claude-code", "model": "scripted", "api": a.api})


if __name__ == "__main__":
    sys.exit(main())
