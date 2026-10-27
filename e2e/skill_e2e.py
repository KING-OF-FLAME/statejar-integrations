"""End to end with the real Claude Code CLI and the agent skill alone (skills/statejar, no plugin): Claude Code hands
the skill to the model, and a model following it stores a fact in one session and recalls it in a fresh one.

It makes a throwaway StateJar account (deleted at the end), installs the skill as a user would (copied to
~/.claude/skills/statejar/ in an isolated HOME) and StateJar's MCP server as the repository's README says
(`claude mcp add --transport http statejar <url> --header "Authorization: Bearer ..."`), then runs two `claude -p`
sessions against e2e/scripted_model.py with --skill (ANTHROPIC_BASE_URL points at it):

  session 1  states facts; the stand-in model asserts the skill was offered (its name and description in the model's
             input, and the Skill tool), invokes it, and once the skill's instructions are in the conversation does
             what they say: StateJar's MCP `remember` with the user's words
  session 2  a fresh process asks about them: the skill is offered again, invoked, and its instructions followed:
             StateJar's MCP `search` with the question, whose output the stand-in repeats verbatim

The stand-in never invents an answer, and it ends a turn saying what was missing when the skill is not offered or not
loaded, so a run fails visibly instead of passing by accident. Everything else is real: Claude Code, its skill
discovery, StateJar's REST API and its MCP server. Run it against a local or staging StateJar, never production:

    python3 e2e/skill_e2e.py --claude "$(command -v claude)" --api http://127.0.0.1:8000/api/v1 \\
        --mcp http://127.0.0.1:8000/mcp --out /tmp/skill-e2e

With --cli <path to statejar> it also takes the skill's second way, on its own throwaway account: no MCP server, and
the `statejar` command (0.7.0 or later) the skill's table gives, run through Claude Code's Bash tool (`statejar add`
in one session, `statejar search` in a fresh one).

With --discovery-only it needs no StateJar (CI runs it on every change): one session, no MCP server; the skill must
be offered and loaded, and with no StateJar tools the model must turn to the skill's `statejar add` command (which
Claude Code, given no permission for it, does not run). The account's password and key are made here and never
printed or written: the MCP header is stored as `Bearer ${STATEJAR_API_KEY}`, which Claude Code expands from its
environment, and the CLI reads STATEJAR_API_KEY.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from common import (FACTS, QUESTION, REPO, Api, free_port, refuse_production, report, throwaway_account,
                    wait_for_version)

SKILL = REPO / "skills" / "statejar"
SCOPE = "mcp"                       # the MCP tools' default scope: the skill names none, so a model passes none
ALLOW = ["--allowedTools", "mcp__statejar__remember", "mcp__statejar__search"]


def claude(binary: str, out: Path, env: dict, *args: str, name: str = "") -> str:
    r = subprocess.run([binary, *args], env=env, cwd=out / "work", stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=600)
    if name:
        (out / f"{name}.stdout").write_text(r.stdout)
        (out / f"{name}.stderr").write_text(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"claude {args[0]} exited {r.returncode}: {r.stderr.strip()[-300:]}")
    return r.stdout


@contextlib.contextmanager
def skill_model(out: Path):
    """The stand-in model following skills/statejar/SKILL.md; yields its port and the skill-log path."""
    port, log = free_port(), out / "skill-log.jsonl"
    proc = subprocess.Popen([sys.executable, str(REPO / "e2e" / "scripted_model.py"), "--port", str(port),
                             "--log", str(out / "model-requests.jsonl"), "--skill", str(SKILL / "SKILL.md"),
                             "--skill-log", str(log)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        yield port, log
    finally:
        proc.terminate()


def skill_rows(log: Path, since: int = 0) -> list[dict]:
    return [json.loads(x) for x in log.read_text().splitlines()][since:] if log.exists() else []


def cli_route(a: argparse.Namespace, out: Path, env: dict, log: Path) -> list:
    """The skill's second way, on its own account: no MCP server; `statejar` on PATH with STATEJAR_API_KEY."""
    checks: list = []
    home = out / "home-cli"
    shutil.copytree(SKILL, home / ".claude" / "skills" / "statejar")
    api = Api(a.api)
    allow = ["--allowedTools", "Bash(statejar add *)", "Bash(statejar search *)"]
    with throwaway_account(api, "skill-cli", checks) as key:
        cenv = {**env, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "STATEJAR_API_KEY": key,
                "STATEJAR_BASE_URL": a.api, "STATEJAR_NO_KEYRING": "1",
                "STATEJAR_CONFIG_DIR": str(home / ".config" / "statejar"),
                "PATH": f"{Path(a.cli).resolve().parent}{os.pathsep}{env.get('PATH', '')}"}
        since = len(skill_rows(log))
        answer3 = claude(a.claude, out, cenv, "-p", FACTS, *allow, name="session3-cli")
        ran = [r["command"] for r in skill_rows(log, since) if r.get("command")]
        versions = wait_for_version(api, "default", limit_s=60)
        handle = versions[-1] if versions else ""
        state = api.must("GET", f"/memory/state/{handle}")["state"] if versions else {}
        checks.append(("CLI session 1: following the skill, `statejar add` stored the user's facts",
                       ran[:1] == [f"statejar add '{FACTS}'"] and handle in answer3
                       and all(x in json.dumps(state) for x in ("Meera", "Pune", "65000")),
                       f"{ran[:1]} -> {len(versions)} version(s)"))
        since = len(skill_rows(log))
        answer4 = claude(a.claude, out, cenv, "-p", QUESTION, *allow, name="session4-cli")
        ran = [r["command"] for r in skill_rows(log, since) if r.get("command")]
        checks.append(("CLI session 2 (a fresh process): `statejar search` recalled the budget with its handle",
                       ran[:1] == [f"statejar search '{QUESTION}'"] and "65000" in answer4 and handle in answer4,
                       f"{ran[:1]}"))
    return checks


def session_checks(log: Path, since: int, label: str) -> tuple[list, int]:
    """What the stand-in saw in one session's requests (the skill log's lines from `since` on)."""
    rows = skill_rows(log, since)
    first = rows[0] if rows else {}
    loaded = [r for r in rows if r.get("invoked")]
    return [
        (f"{label}: Claude Code handed the model the skill (name and description, and the Skill tool)",
         bool(first.get("listed") and first.get("skill_tool")),
         f"first request: listed={first.get('listed')}, Skill tool={first.get('skill_tool')}"),
        (f"{label}: invoking it put the skill's instructions in the conversation",
         bool(loaded) and all(r.get("instructions") for r in loaded),
         f"{len(loaded)} request(s) after the Skill call"),
    ], len(rows)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--claude", default=shutil.which("claude") or "claude")
    p.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    p.add_argument("--mcp", default="http://127.0.0.1:8000/mcp")
    p.add_argument("--out", required=True)
    p.add_argument("--cli", help="also the skill's CLI route: this statejar executable (0.7.0 or later)")
    p.add_argument("--discovery-only", action="store_true", help="no StateJar: check only that the skill is "
                   "offered and loaded, and that the model then turns to the skill's command")
    a = p.parse_args()
    refuse_production(a.api, a.mcp)
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "work").mkdir(parents=True)
    home = out / "home"
    shutil.copytree(SKILL, home / ".claude" / "skills" / "statejar")          # the README's install for Claude Code
    version = subprocess.run([a.claude, "--version"], capture_output=True, text=True).stdout.strip()
    checks: list = []
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ANTHROPIC_", "CLAUDE_", "STATEJAR_")) or k == "CLAUDE_CODE_GIT_BASH_PATH"}
    with contextlib.ExitStack() as stack:
        port, log = stack.enter_context(skill_model(out))
        env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_API_KEY": "scripted-model",
                    "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "DISABLE_AUTOUPDATER": "1"})
        if a.discovery_only:
            claude(a.claude, out, env, "-p", FACTS, name="session1")
            found, _ = session_checks(log, 0, "session 1")
            checks += found
            ran = [r["command"] for r in skill_rows(log) if r.get("command")]
            checks.append(("with no StateJar MCP tools, the model turned to the skill's `statejar add` command",
                           ran[:1] == [f"statejar add '{FACTS}'"], f"{ran[:1]}"))
            return report(checks, out, {"client": "claude-code", "version": version, "model": "scripted",
                                        "mode": "discovery-only"})
        api = Api(a.api)
        key = stack.enter_context(throwaway_account(api, "skill", checks))
        env["STATEJAR_API_KEY"] = key
        claude(a.claude, out, env, "mcp", "add", "--transport", "http", "--scope", "user", "statejar", a.mcp,
               "--header", "Authorization: Bearer ${STATEJAR_API_KEY}")
        listed = claude(a.claude, out, env, "mcp", "list", name="mcp-list")
        checks.append(("Claude Code connects to StateJar's MCP server with the key from the environment",
                       "statejar" in listed and "Connected" in listed,
                       next((ln.strip() for ln in listed.splitlines() if "statejar" in ln), "not listed")))

        answer1 = claude(a.claude, out, env, "-p", FACTS, *ALLOW, name="session1")
        found, seen = session_checks(log, 0, "session 1")
        checks += found
        versions = wait_for_version(api, SCOPE, limit_s=60)
        handle = versions[-1] if versions else ""
        state = api.must("GET", f"/memory/state/{handle}")["state"] if versions else {}
        checks.append(("session 1: following the skill, MCP `remember` stored the user's facts",
                       bool(versions) and all(x in json.dumps(state) for x in ("Meera", "Pune", "65000"))
                       and handle in answer1, f"{len(versions)} version(s), handle {handle[:16]}…"))

        answer2 = claude(a.claude, out, env, "-p", QUESTION, *ALLOW, name="session2")
        found, _ = session_checks(log, seen, "session 2 (a fresh process)")
        checks += found
        checks.append(("session 2: following the skill, MCP `search` recalled the budget with its handle",
                       "65000" in answer2 and handle in answer2, "the tool's output, repeated by the stand-in model"))
        audit = api.must("GET", "/audit").get("entries") or []
        checks.append(("the recall is in the audit trail", any(e.get("handle_used") == handle for e in audit),
                       f"{len(audit)} audit entries"))
        if a.cli:                   # while the stand-in model still runs
            checks += cli_route(a, out, {k: v for k, v in env.items() if k != "STATEJAR_API_KEY"}, log)
    return report(checks, out, {"client": "claude-code", "version": version, "model": "scripted", "api": a.api,
                                "skill": "skills/statejar"})


if __name__ == "__main__":
    sys.exit(main())
