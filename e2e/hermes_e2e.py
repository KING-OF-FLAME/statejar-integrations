"""End to end with the real Hermes Agent CLI: a fact stated in one session is recalled in a fresh one.

It makes a throwaway StateJar account (deleted at the end), installs hermes/statejar as a user memory provider in an
isolated HERMES_HOME ($HERMES_HOME/plugins/statejar, `memory.provider: statejar`, the key in that profile's .env as
`hermes memory setup` writes it), and runs two `hermes chat -q` sessions:

  session 1  states facts; the provider queues the message (secrets removed) and sends it when the session ends
  session 2  a fresh process asks about them; the provider recalls the memory before the model call, and the model
             can call the provider's statejar_search tool

The model: `--model scripted` (default) is e2e/scripted_model.py as a named custom provider, a stand-in that answers
"OK" or calls StateJar's search tool and repeats its output verbatim, for machines with no model;
`--model ollama:<name>` uses a real model on a local Ollama. Everything else is real: Hermes, the provider, StateJar's
REST API. Run it against a local or staging StateJar, never production:

    python3 e2e/hermes_e2e.py --hermes "$(command -v hermes)" --api http://127.0.0.1:8000/api/v1 --out /tmp/hermes-e2e
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

SCOPE = "hermes"


def chat(binary: str, out: Path, env: dict, provider: str, model: str, prompt: str, name: str) -> str:
    r = subprocess.run([binary, "chat", "-q", prompt, "-Q", "--provider", provider, "-m", model],
                       env=env, cwd=out / "work", stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    (out / f"{name}.stdout").write_text(r.stdout)
    (out / f"{name}.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"hermes chat ({name}) exited {r.returncode}; see {out / (name + '.stderr')}")
    return r.stdout


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--hermes", default=shutil.which("hermes") or "hermes")
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
    home = out / "hermes-home"
    (out / "work").mkdir(parents=True)
    shutil.copytree(REPO / "hermes" / "statejar", home / "plugins" / "statejar")
    api = Api(a.api)
    checks: list = []
    log = out / "model-requests.jsonl"
    with contextlib.ExitStack() as stack:
        key = stack.enter_context(throwaway_account(api, "hermes", checks))
        if a.model == "scripted":
            port = stack.enter_context(scripted_model(log))
            provider, base, model = "scripted", f"http://127.0.0.1:{port}/v1", "scripted"
        else:
            provider, base, model = "local-ollama", a.ollama_url, a.model.split(":", 1)[1]
        (home / "config.yaml").write_text(
            f"model: {model}\n"
            f"providers:\n  {provider}:\n    api: {base}\n    transport: chat_completions\n    default_model: {model}\n"
            f"    models:\n      {model}:\n        context_length: 131072\n"
            "memory:\n  provider: statejar\n")
        (home / ".env").write_text(f"STATEJAR_API_KEY={key}\n")          # where `hermes memory setup` puts it
        (home / ".env").chmod(0o600)
        (home / "statejar.json").write_text(json.dumps({"api_url": a.api, "scope": SCOPE}))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("STATEJAR_", "HERMES_", "OPENAI_"))}
        env.update({"HERMES_HOME": str(home), "HOME": str(out), "XDG_CONFIG_HOME": str(out / ".config")})

        chat(a.hermes, out, env, provider, model, FACTS, "session1")
        check, handle = stored_check(api, SCOPE)
        checks.append(check)
        answer = chat(a.hermes, out, env, provider, model, QUESTION, "session2")
        if a.model == "scripted":
            requests = [json.loads(x) for x in log.read_text().splitlines()]
            s2 = [r for r in requests if r["path"].endswith("/chat/completions")
                  and QUESTION in json.dumps(r["body"].get("messages"))]
            first = json.dumps(s2[0]["body"].get("messages")) if s2 else ""
            checks.append(("session 2: the provider put the memory in front of the model",
                           "65000" in first and handle in first, "budget and handle in the model's input"))
            tool_round = [r for r in s2 if any(m.get("role") == "tool" for m in r["body"].get("messages") or [])]
            checks.append(("session 2: Hermes called the provider's statejar_search and got the memory back",
                           bool(tool_round) and "65000" in answer and handle in answer,
                           "the tool's output, repeated by the stand-in model"))
        else:
            checks.append(("session 2: the model answered with the stored budget", "65000" in answer.replace(",", ""),
                           answer.strip().splitlines()[-1][:120] if answer.strip() else "empty"))
        audit = api.must("GET", "/audit").get("entries") or []
        checks.append(("every recall is in the audit trail", len(audit) >= 2, f"{len(audit)} audit entries"))
    return report(checks, out, {"client": "hermes", "model": a.model, "api": a.api})


if __name__ == "__main__":
    sys.exit(main())
