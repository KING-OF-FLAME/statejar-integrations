"""End to end with the real OpenCode CLI: a fact stated in one session is recalled in a fresh one.

It makes a throwaway StateJar account (deleted at the end), puts opencode/statejar.js in an isolated OpenCode config
(its global plugins folder) with StateJar's MCP server in opencode.json, and runs two `opencode run` sessions:

  session 1  states facts; the plugin queues the message (secrets removed) and sends it when OpenCode exits
  session 2  a fresh process asks about them; the plugin recalls the memory into the system prompt, and the model can
             call StateJar's MCP tools

The model: `--model scripted` (default) is e2e/scripted_model.py behind OpenCode's OpenAI-compatible provider, a
stand-in that answers "OK" or calls StateJar's `search` tool and repeats its output verbatim, for machines with no
model; `--model ollama:<name>` uses a real model on a local Ollama. Everything else is real: OpenCode, the plugin,
StateJar's REST API and its MCP server. Run it against a local or staging StateJar, never production:

    python3 e2e/opencode_e2e.py --opencode "$(command -v opencode)" --api http://127.0.0.1:8000/api/v1 \\
        --mcp http://127.0.0.1:8000/mcp --out /tmp/opencode-e2e

The account's password and key are made here and never printed; the key reaches OpenCode as STATEJAR_API_KEY.
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

SCOPE = "opencode"


def run_session(binary: str, out: Path, env: dict, model: str, prompt: str, name: str) -> str:
    r = subprocess.run([binary, "run", "--model", model, prompt], env=env, cwd=out / "work", stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=600)
    (out / f"{name}.stdout").write_text(r.stdout)
    (out / f"{name}.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"opencode run ({name}) exited {r.returncode}; see {out / (name + '.stderr')}")
    return r.stdout


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--opencode", default=shutil.which("opencode") or "opencode")
    p.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    p.add_argument("--mcp", default="http://127.0.0.1:8000/mcp")
    p.add_argument("--model", default="scripted", help="scripted, or ollama:<model name>")
    p.add_argument("--ollama-url", default="http://127.0.0.1:11434/v1")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    refuse_production(a.api, a.mcp)
    if a.model != "scripted" and not a.model.startswith("ollama:"):
        raise SystemExit("--model is scripted or ollama:<model name>")
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    home = out / "home"
    config_dir = home / ".config" / "opencode"
    (config_dir / "plugins").mkdir(parents=True)
    (out / "work").mkdir()
    shutil.copy(REPO / "opencode" / "statejar.js", config_dir / "plugins" / "statejar.js")
    api = Api(a.api)
    checks: list = []
    log = out / "model-requests.jsonl"
    with contextlib.ExitStack() as stack:
        key = stack.enter_context(throwaway_account(api, "opencode", checks))
        if a.model == "scripted":
            port = stack.enter_context(scripted_model(log))
            provider, base, model_id = "scripted", f"http://127.0.0.1:{port}/v1", "scripted"
        else:
            provider, base, model_id = "ollama", a.ollama_url, a.model.split(":", 1)[1]
        config = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "provider": {provider: {"npm": "@ai-sdk/openai-compatible", "name": provider,
                                    "options": {"baseURL": base, "apiKey": "not-a-key"},
                                    "models": {model_id: {"name": model_id, "tool_call": True}}}},
            "mcp": {"statejar": {"type": "remote", "url": a.mcp, "oauth": False,
                                 "headers": {"Authorization": "Bearer {env:STATEJAR_API_KEY}"}}},
        }
        (config_dir / "opencode.json").write_text(json.dumps(config, indent=1))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("STATEJAR_", "OPENCODE_"))}
        env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"),
                    "XDG_CACHE_HOME": str(home / ".cache"), "XDG_STATE_HOME": str(home / ".local" / "state"),
                    "STATEJAR_API_KEY": key, "STATEJAR_API_URL": a.api, "STATEJAR_SCOPE": SCOPE})
        model = f"{provider}/{model_id}"

        run_session(a.opencode, out, env, model, FACTS, "session1")
        check, handle = stored_check(api, SCOPE)
        checks.append(check)
        answer = run_session(a.opencode, out, env, model, QUESTION, "session2")
        if a.model == "scripted":
            requests = [json.loads(x) for x in log.read_text().splitlines()]
            s2 = [r for r in requests if r["path"].endswith("/chat/completions")
                  and QUESTION in json.dumps(r["body"].get("messages"))]
            first = json.dumps(s2[0]["body"].get("messages")) if s2 else ""
            checks.append(("session 2: the plugin put the memory in front of the model",
                           "65000" in first and handle in first, "budget and handle in the system prompt"))
            tool_round = [r for r in s2 if any(m.get("role") == "tool" for m in r["body"].get("messages") or [])]
            checks.append(("session 2: OpenCode called StateJar's MCP search and got the memory back",
                           bool(tool_round) and "65000" in answer and handle in answer,
                           "the tool's output, repeated by the stand-in model"))
        else:
            checks.append(("session 2: the model answered with the stored budget", "65000" in answer.replace(",", ""),
                           answer.strip().splitlines()[-1][:120] if answer.strip() else "empty"))
        audit = api.must("GET", "/audit").get("entries") or []
        checks.append(("every recall is in the audit trail", len(audit) >= 2, f"{len(audit)} audit entries"))
    return report(checks, out, {"client": "opencode", "model": a.model, "api": a.api})


if __name__ == "__main__":
    sys.exit(main())
