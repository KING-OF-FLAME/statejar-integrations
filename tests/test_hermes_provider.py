"""The Hermes Agent memory provider (hermes/statejar), loaded by Hermes's own discovery from $HERMES_HOME/plugins and
driven by Hermes's MemoryManager against the stand-in StateJar API. Needs a Hermes Agent install: set HERMES_PYTHON to
its interpreter (the test is skipped without one). The provider's secret scrubbing must match the Python hook's."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import API, HOOK, KEY, ROOT, TOKEN
from test_opencode_plugin import CORPUS

PROVIDER = ROOT / "hermes" / "statejar"
HERMES_PYTHON = os.environ.get("HERMES_PYTHON", "")


def load_provider_module():
    """Import the provider without Hermes: a stand-in `agent.memory_provider` with the ABC's name only."""
    agent = types.ModuleType("agent")
    mp = types.ModuleType("agent.memory_provider")
    mp.MemoryProvider = type("MemoryProvider", (), {})
    sys.modules.setdefault("agent", agent)
    sys.modules.setdefault("agent.memory_provider", mp)
    spec = importlib.util.spec_from_file_location("statejar_hermes", PROVIDER / "__init__.py",
                                                  submodule_search_locations=[str(PROVIDER)])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_provider_removes_the_same_secrets_as_the_hook():
    provider = load_provider_module()
    spec = importlib.util.spec_from_file_location("statejar_hook", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    for text in CORPUS:
        assert provider.scrub(text) == hook.scrub(text), text


def test_the_plugin_declares_the_hooks_it_implements():
    meta = (PROVIDER / "plugin.yaml").read_text()
    assert "name: statejar" in meta and "pip_dependencies" not in meta     # standard library only
    source = (PROVIDER / "__init__.py").read_text()
    for hook in ("on_session_end", "on_pre_compress", "on_memory_write", "on_session_switch"):
        assert f"  - {hook}" in meta and f"def {hook}(" in source


@pytest.mark.skipif(not HERMES_PYTHON, reason="set HERMES_PYTHON to a Hermes Agent 0.19+ interpreter")
def test_hermes_loads_the_provider_and_it_recalls_captures_and_answers_tools(api, tmp_path):
    env, _ = api
    home = tmp_path / "hermes-home"
    shutil.copytree(PROVIDER, home / "plugins" / "statejar")
    (home / "statejar.json").write_text(json.dumps({"api_url": env["CLAUDE_PLUGIN_OPTION_API_URL"], "scope": "hermes-test"}))
    API.memory = "Known user state:\nconstraints.budget.max: INR 80000"
    run_env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN", "STATEJAR_"))}
    run_env.update({"HERMES_HOME": str(home), "STATEJAR_API_KEY": KEY, "TOKEN": TOKEN,
                    "STATEJAR_CONFIG_DIR": str(tmp_path / "nocreds")})
    r = subprocess.run([HERMES_PYTHON, str(ROOT / "tests" / "hermes_check.py")], capture_output=True, text=True,
                       env=run_env, cwd=tmp_path, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "statejar" in out["found"] and out["loaded"] and out["name"] == "statejar"
    assert out["tools"] == ["statejar_forget", "statejar_list", "statejar_remember", "statejar_search"]
    assert "INR 80000" in out["prefetched"] and out["searched"]["handle_used"].startswith("shm_")
    sent = [c for c in API.calls if c[1].endswith("/memory/ingest")]
    assert len(sent) == 1                                          # three messages, one batch
    body = sent[0][2]
    assert body["session_tag"] == "hermes-test" and body["async"] is True
    assert "Kavya" in body["text"] and "email" in body["text"] and TOKEN not in body["text"]
    assert "Noted." not in body["text"]                            # the assistant's reply is never sent
    assert sent[0][3]["x-statejar-client"] == "hermes" and sent[0][3]["x-api-key"] == KEY
    assert out["status"]["messages_sent"] == 3 and out["status"]["key"] == "…" + KEY[-4:]
    for word in ("Kavya", "80000", KEY):
        assert word not in json.dumps(out["status"])
    assert "scope: hermes-test" in out["system"]
