"""The Codex side of the plugin: its manifest, MCP file, hooks and skills in the shapes Codex 0.163 reads
(core-plugins/src/manifest.rs, codex-mcp/src/plugin_config.rs, config/src/hook_config.rs at rust-v0.163.0), and the
shared hook script run as Codex runs it (`--client codex`, options from STATEJAR_* variables). The run with the real
Codex binary, a fact stored in one session and recalled in a fresh one, is e2e/codex_e2e.py."""

from __future__ import annotations

import json
import re

from conftest import API, KEY, PLUGIN, ROOT, run

CODEX_EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "Stop": "stop",
                "PreCompact": "pre-compact", "SessionEnd": "session-end"}


def codex_env(env: dict, tmp_path) -> dict:
    """What Codex gives a plugin hook: no CLAUDE_PLUGIN_OPTION_*, the key and options from the user's environment."""
    url = env["CLAUDE_PLUGIN_OPTION_API_URL"]
    out = {k: v for k, v in env.items() if not k.startswith("CLAUDE_PLUGIN_OPTION_")}
    out.update({"STATEJAR_API_KEY": KEY, "STATEJAR_API_URL": url, "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
                "PLUGIN_ROOT": str(PLUGIN), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
                "PLUGIN_DATA": str(tmp_path / "codex-data"), "CLAUDE_PLUGIN_DATA": str(tmp_path / "codex-data")})
    out.pop("STATEJAR_CONFIG_DIR", None)
    return out


def codex_prompt(env: dict, text: str) -> str:
    return run(env, "prompt", {"session_id": "s", "turn_id": "t", "hook_event_name": "UserPromptSubmit",
                               "prompt": text}, "--client", "codex")


def test_the_codex_manifest_points_at_the_codex_files():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "statejar"
    claude = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["version"] == claude["version"]
    for field, path in (("skills", "./codex/skills"), ("mcpServers", "./codex/mcp.json"), ("hooks", "./codex/hooks.json")):
        assert manifest[field] == path                       # Codex wants "./"-relative paths inside the plugin
        assert (PLUGIN / path).exists()
    assert manifest["interface"]["privacyPolicyURL"].startswith("https://statejar.com/")


def test_the_codex_mcp_file_takes_the_key_from_the_environment():
    server = json.loads((PLUGIN / "codex" / "mcp.json").read_text())["mcpServers"]["statejar"]
    assert server == {"url": "https://statejar.com/mcp", "bearer_token_env_var": "STATEJAR_API_KEY"}


def test_the_codex_hooks_are_shell_commands_for_every_event():
    hooks_file = json.loads((PLUGIN / "codex" / "hooks.json").read_text())
    assert set(hooks_file) <= {"description", "hooks"}       # Codex's HooksFile denies unknown top-level fields
    hooks = hooks_file["hooks"]
    assert set(hooks) == set(CODEX_EVENTS)
    for event, sub in CODEX_EVENTS.items():
        (handler,) = [h for group in hooks[event] for h in group["hooks"]]
        assert handler["type"] == "command" and "args" not in handler        # Codex has no exec form
        assert handler["command"] == f'python3 "$PLUGIN_ROOT/scripts/statejar_hook.py" {sub} --client codex'
        assert handler["commandWindows"].endswith(f"{sub} --client codex")
    assert hooks["Stop"][0]["hooks"][0]["async"] is True
    assert hooks["SessionEnd"][0]["hooks"][0]["timeout"] <= 3                 # Codex caps SessionEnd at 3 s


def test_the_codex_skills_use_no_claude_only_syntax():
    names = sorted(p.parent.name for p in (PLUGIN / "codex" / "skills").glob("*/SKILL.md"))
    assert names == ["forget", "pause", "remember", "resume", "search", "status"]
    for path in (PLUGIN / "codex" / "skills").glob("*/SKILL.md"):
        text = path.read_text()
        front = re.match(r"---\n(.*?)\n---\n", text, re.S)
        assert front and re.search(r"^name: \S", front.group(1), re.M) and re.search(r"^description: \S", front.group(1), re.M)
        assert "${user_config" not in text and "!`" not in text and "$ARGUMENTS" not in text


def test_one_marketplace_serves_both_clients():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    (entry,) = market["plugins"]
    assert entry["source"] == "./plugins/statejar"
    assert (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").exists()
    assert (ROOT / entry["source"] / ".codex-plugin" / "plugin.json").exists()


def test_codex_hooks_read_options_from_the_environment_and_use_their_own_folder(api, tmp_path):
    env, _ = api
    cx = codex_env(env, tmp_path)
    out = json.loads(run(cx, "session-start", {"hook_event_name": "SessionStart", "source": "startup"}, "--client", "codex"))
    assert 'session="codex"' in out["hookSpecificOutput"]["additionalContext"]       # the default scope for Codex
    for i in range(3):
        codex_prompt(cx, f"Fact number {i}: I work as a nurse in Pune.")
    run(cx, "stop", {"hook_event_name": "Stop", "stop_hook_active": False}, "--client", "codex")
    (sent,) = [c for c in API.calls if c[1].endswith("/memory/ingest")]
    assert sent[2]["session_tag"] == "codex" and sent[3]["x-statejar-client"] == "codex"
    assert sent[3]["user-agent"].startswith("statejar-codex/")
    # the queue lives beside `statejar login`'s file, not in PLUGIN_DATA: a command the model runs finds it too
    folder = tmp_path / "xdg" / "statejar" / "clients" / "codex"
    assert (folder / "state.json").exists() and not (tmp_path / "codex-data").exists()
    status = json.loads(run({k: v for k, v in cx.items() if not k.startswith(("PLUGIN_", "CLAUDE_PLUGIN_"))},
                            "status", None, "--client", "codex"))
    assert status["client"] == "codex" and status["prompts_sent"] == 3 and status["key"] == "…" + KEY[-4:]


def test_codex_scope_capture_and_recall_options(api, tmp_path):
    env, _ = api
    cx = {**codex_env(env, tmp_path), "STATEJAR_SCOPE": "my-project", "STATEJAR_AUTO_CAPTURE": "false"}
    API.memory = "Known user state:\nfacts.city: Pune"
    added = json.loads(codex_prompt(cx, "Where do I live again?"))["hookSpecificOutput"]
    assert "Pune" in added["additionalContext"]
    (query,) = [c for c in API.calls if c[1].endswith("/memory/query")]
    assert query[2]["session_tag"] == "my-project"
    assert not (tmp_path / "xdg" / "statejar" / "clients" / "codex" / "queue.jsonl").exists()   # capture is off
    codex_prompt({**cx, "STATEJAR_AUTO_CAPTURE": "true"}, "$statejar:search what is my budget")     # a skill call
    assert not (tmp_path / "xdg" / "statejar" / "clients" / "codex" / "queue.jsonl").exists()


def test_codex_pause_says_how_to_resume_in_codex(api, tmp_path):
    env, _ = api
    cx = codex_env(env, tmp_path)
    assert "$statejar:resume" in run(cx, "pause", None, "--client", "codex")
    out = json.loads(run(cx, "session-start", {"hook_event_name": "SessionStart"}, "--client", "codex"))
    assert "$statejar:resume" in out["hookSpecificOutput"]["additionalContext"]
    run(cx, "resume", None, "--client", "codex")
    no_key = {k: v for k, v in cx.items() if k != "STATEJAR_API_KEY"}
    out = json.loads(run(no_key, "session-start", {"hook_event_name": "SessionStart"}, "--client", "codex"))
    assert "STATEJAR_API_KEY" in out["hookSpecificOutput"]["additionalContext"]


def test_an_unknown_client_is_refused(api):
    env, _ = api
    import subprocess
    import sys
    from conftest import HOOK
    r = subprocess.run([sys.executable, str(HOOK), "status", "--client", "nope"], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "--client" in r.stderr
