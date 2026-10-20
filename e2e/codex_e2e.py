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
import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import FACTS, QUESTION, REPO, Api, refuse_production, report, scripted_model, stored_check, throwaway_account

SCOPE = "codex"


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--codex", default=shutil.which("codex") or "codex")
    p.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    p.add_argument("--mcp", default="http://127.0.0.1:8000/mcp")
    p.add_argument("--model", default="scripted", help="scripted, or ollama:<model name>")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    refuse_production(a.api, a.mcp)
    if a.model != "scripted" and not a.model.startswith("ollama:"):
        raise SystemExit("--model is scripted or ollama:<model name>")
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "work").mkdir(parents=True)
    (out / "home").mkdir()
    api = Api(a.api)
    checks: list = []
    log = out / "model-requests.jsonl"
    with contextlib.ExitStack() as stack:
        key = stack.enter_context(throwaway_account(api, "codex", checks))
        env = {**os.environ, "CODEX_HOME": str(out / "home"), "HOME": str(out / "home"),
               "XDG_CONFIG_HOME": str(out / "home" / ".config"),
               "STATEJAR_API_KEY": key, "STATEJAR_API_URL": a.api, "STATEJAR_SCOPE": SCOPE}
        env.pop("STATEJAR_DATA_DIR", None)
        install(a.codex, out, a.mcp, env)
        model_args: list[str] = []
        if a.model == "scripted":
            port = stack.enter_context(scripted_model(log))
            config = out / "home" / "config.toml"
            config.write_text('model = "scripted"\nmodel_provider = "scripted"\n\n' + config.read_text() +
                              f'\n[model_providers.scripted]\nname = "scripted"\nbase_url = "http://127.0.0.1:{port}/v1"\n'
                              'wire_api = "responses"\n')
        else:
            model_args = ["--oss", "--local-provider", "ollama", "-m", a.model.split(":", 1)[1]]

        exec_session(a.codex, out, env, model_args, FACTS, "session1")
        check, handle = stored_check(api, SCOPE)
        checks.append(check)
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
    return report(checks, out, {"client": "codex", "model": a.model, "api": a.api})


if __name__ == "__main__":
    sys.exit(main())
