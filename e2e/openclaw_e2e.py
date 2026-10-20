"""End to end with the real OpenClaw CLI: a fact stated in one session is recalled in a fresh one.

It makes a throwaway StateJar account (deleted at the end), installs this repository's openclaw/ plugin into an
isolated OpenClaw state directory (`openclaw plugins install --link`, then `openclaw statejar init`, which selects the
memory slot and grants the hooks conversation access), and runs two `openclaw agent --local` turns with different
session ids:

  session 1  states facts; autoCapture queues the message (secrets removed) and sends it at the session's end
  session 2  a fresh session asks about them; autoRecall puts the memory before the model, and the model can call
             memory_search

The model: `--model scripted` (default) is e2e/scripted_model.py as an OpenAI-compatible custom provider, a stand-in
that answers "OK" or calls the search tool and repeats its output verbatim, for machines with no model;
`--model ollama:<name>` uses a real model on a local Ollama. Everything else is real. OpenClaw needs Node 24.16+ on
PATH. Run it against a local or staging StateJar, never production:

    python3 e2e/openclaw_e2e.py --openclaw "$(command -v openclaw)" --api http://127.0.0.1:8000/api/v1 --out /tmp/oc-e2e
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

SCOPE = "openclaw"


def openclaw(binary: str, out: Path, env: dict, *args: str, stdin: str | None = None, name: str = "") -> str:
    r = subprocess.run([binary, *args], env=env, cwd=out / "work", input=stdin,
                       stdin=None if stdin is not None else subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    if name:
        (out / f"{name}.stdout").write_text(r.stdout)
        (out / f"{name}.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"openclaw {' '.join(args[:2])} exited {r.returncode}: {r.stderr.strip()[-400:]}")
    return r.stdout


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--openclaw", default=shutil.which("openclaw") or "openclaw")
    p.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    p.add_argument("--model", default="scripted", help="scripted, or ollama:<model name>")
    p.add_argument("--ollama-url", default="http://127.0.0.1:11434/v1")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    refuse_production(a.api)
    if a.model != "scripted" and not a.model.startswith("ollama:"):
        raise SystemExit("--model is scripted or ollama:<model name>")
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "work").mkdir(parents=True)
    plugin = out / "plugin"
    shutil.copytree(REPO / "openclaw", plugin)
    api = Api(a.api)
    checks: list = []
    log = out / "model-requests.jsonl"
    with contextlib.ExitStack() as stack:
        key = stack.enter_context(throwaway_account(api, "openclaw", checks))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("STATEJAR_", "OPENCLAW_"))}
        env.update({"HOME": str(out / "home"), "OPENCLAW_STATE_DIR": str(out / "home" / ".openclaw"),
                    "XDG_CONFIG_HOME": str(out / "home" / ".config"), "STATEJAR_API_KEY": key})
        openclaw(a.openclaw, out, env, "plugins", "install", "--link", str(plugin), "--force", "--accept-capabilities")
        openclaw(a.openclaw, out, env, "statejar", "init", "--scope", SCOPE)
        if a.model == "scripted":
            port = stack.enter_context(scripted_model(log))
            provider, base, model = "scripted", f"http://127.0.0.1:{port}/v1", "scripted"
        else:
            provider, base, model = "ollama-local", a.ollama_url, a.model.split(":", 1)[1]
        patch = {
            "agents": {"defaults": {"model": {"primary": f"{provider}/{model}"}}},
            "models": {"providers": {provider: {"baseUrl": base, "apiKey": "not-a-key", "api": "openai-completions",
                                                "models": [{"id": model, "name": model, "contextWindow": 131072,
                                                            "maxTokens": 2048}]}}},
            "plugins": {"entries": {"statejar": {"config": {"apiUrl": a.api}}}},
        }
        openclaw(a.openclaw, out, env, "config", "patch", "--stdin", stdin=json.dumps(patch))

        openclaw(a.openclaw, out, env, "agent", "--local", "--session-id", "e2e-session-1", "-m", FACTS, name="session1")
        check, handle = stored_check(api, SCOPE)
        checks.append(check)
        answer = openclaw(a.openclaw, out, env, "agent", "--local", "--session-id", "e2e-session-2", "-m", QUESTION,
                          name="session2")
        if a.model == "scripted":
            requests = [json.loads(x) for x in log.read_text().splitlines()]
            s2 = [r for r in requests if r["path"].endswith("/chat/completions")
                  and QUESTION in json.dumps(r["body"].get("messages"))]
            first = json.dumps(s2[0]["body"].get("messages")) if s2 else ""
            checks.append(("session 2: autoRecall put the memory in front of the model",
                           "65000" in first and handle in first, "budget and handle in the model's input"))
            tool_round = [r for r in s2 if any(m.get("role") == "tool" for m in r["body"].get("messages") or [])]
            checks.append(("session 2: OpenClaw called memory_search and got the memory back",
                           bool(tool_round) and "65000" in answer, "the tool's output, repeated by the stand-in model"))
        else:
            checks.append(("session 2: the model answered with the stored budget", "65000" in answer.replace(",", ""),
                           answer.strip().splitlines()[-1][:120] if answer.strip() else "empty"))
        audit = api.must("GET", "/audit").get("entries") or []
        checks.append(("every recall is in the audit trail", len(audit) >= 2, f"{len(audit)} audit entries"))
    return report(checks, out, {"client": "openclaw", "model": a.model, "api": a.api})


if __name__ == "__main__":
    sys.exit(main())
