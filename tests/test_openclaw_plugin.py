"""The OpenClaw memory-slot plugin (openclaw/index.js) run in Node against a recording stand-in for OpenClaw's plugin
API and the stand-in StateJar API: what it registers, what its hooks send, and when. The run with the real OpenClaw
(install, `openclaw statejar init`, two `openclaw agent --local` sessions) is e2e/openclaw_e2e.py."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import API, KEY, ROOT, TOKEN
from test_opencode_plugin import CORPUS, python_scrub

PLUGIN = ROOT / "openclaw"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node 20+")

HARNESS = """
import { pathToFileURL } from "node:url"
const registered = { tools: {}, hooks: {}, capability: null, cli: null }
const api = {
  pluginConfig: JSON.parse(process.env.PLUGIN_CONFIG || "{}"),
  registerTool: (t) => { registered.tools[t.name] = t },
  registerMemoryCapability: (c) => { registered.capability = c },
  on: (name, handler) => { (registered.hooks[name] ??= []).push(handler) },
  registerCli: (registrar, opts) => { registered.cli = opts },
  runtime: { config: {} },
}
const entry = (await import(pathToFileURL(process.env.PLUGIN_ENTRY).href)).default
entry.register(api)
const fire = async (name, event = {}, ctx = {}) => {
  let out
  for (const h of registered.hooks[name] || []) out = (await h(event, ctx)) ?? out
  return out
}
"""


def plugin_dir(tmp_path: Path) -> Path:
    """The plugin beside a stand-in `openclaw/plugin-sdk/plugin-entry` (definePluginEntry returns its argument)."""
    d = tmp_path / "plugin"
    shutil.copytree(PLUGIN, d)
    sdk = d / "node_modules" / "openclaw"
    (sdk / "plugin-sdk").mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({"name": "openclaw", "type": "module",
                                                  "exports": {"./plugin-sdk/plugin-entry": "./plugin-sdk/plugin-entry.js"}}))
    (sdk / "plugin-sdk" / "plugin-entry.js").write_text("export const definePluginEntry = (d) => d\n")
    return d


def node(tmp_path: Path, script: str, env: dict) -> dict:
    d = plugin_dir(tmp_path)
    r = subprocess.run(["node", "--input-type=module", "-e", HARNESS + script], capture_output=True, text=True, timeout=60,
                       env={**env, "PLUGIN_ENTRY": str(d / "index.js")}, cwd=d)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def openclaw_env(env: dict, tmp_path: Path, **config) -> dict:
    url = env["CLAUDE_PLUGIN_OPTION_API_URL"]
    out = {k: v for k, v in env.items() if not k.startswith(("CLAUDE_PLUGIN", "STATEJAR_"))}
    out.update({"STATEJAR_API_KEY": KEY, "STATEJAR_CONFIG_DIR": str(tmp_path / "creds"),
                "PLUGIN_CONFIG": json.dumps({"apiUrl": url, "scope": "oc-test", "dataDir": str(tmp_path / "data"), **config})})
    return out


def test_it_registers_as_the_memory_slot_with_its_tools_and_cli(api, tmp_path):
    env, _ = api
    out = node(tmp_path, """
console.log(JSON.stringify({ kind: entry.kind, id: entry.id, tools: Object.keys(registered.tools).sort(),
  hooks: Object.keys(registered.hooks).sort(), capability: registered.capability, cli: registered.cli }))
""", openclaw_env(env, tmp_path))
    assert out["kind"] == "memory" and out["id"] == "statejar"
    assert out["tools"] == ["memory_get", "memory_search", "statejar_forget", "statejar_remember"]
    assert out["hooks"] == ["agent_end", "before_compaction", "before_prompt_build", "before_reset", "gateway_start",
                            "gateway_stop", "session_end"]
    assert out["capability"] == {"recallToolNames": ["memory_search"]}
    assert out["cli"]["descriptors"][0]["name"] == "statejar"
    manifest = json.loads((PLUGIN / "openclaw.plugin.json").read_text())
    assert manifest["kind"] == "memory" and sorted(manifest["contracts"]["tools"]) == out["tools"]
    assert "apiKey" not in json.dumps(manifest["configSchema"])          # the key never goes in OpenClaw's config


def test_recall_capture_and_batching_inside_a_gateway(api, tmp_path):
    env, _ = api
    API.memory = "Known user state:\nconstraints.budget.max: INR 80000"
    out = node(tmp_path, f"""
await fire("gateway_start")
const recalled = await fire("before_prompt_build", {{ prompt: "x", currentUserMessage: "Which laptop fits my budget?", messages: [] }})
const turn = (text) => fire("agent_end", {{ success: true, messages: [{{ role: "user", content: text }}, {{ role: "assistant", content: "Noted." }}] }})
await turn("I'm Kavya and I live in Bengaluru.")
await turn("My budget is 80000 rupees and my token is {TOKEN}.")
const fs = await import("node:fs")
const queued = fs.readFileSync({json.dumps(str(tmp_path / "data" / "queue.jsonl"))}, "utf8")
await turn("I prefer email for anything official.")
const search = await registered.tools.memory_search.execute("t1", {{ query: "What is my budget?" }})
console.log(JSON.stringify({{ recalled, queued, search }}))
""", openclaw_env(env, tmp_path))
    assert "INR 80000" in out["recalled"]["prependContext"] and "scope oc-test" in out["recalled"]["prependContext"]
    assert TOKEN not in out["queued"] and "[secret: api key]" in out["queued"]
    sent = [c for c in API.calls if c[1].endswith("/memory/ingest")]
    assert len(sent) == 1                                              # inside a gateway: one batch of three
    assert "Kavya" in sent[0][2]["text"] and "email" in sent[0][2]["text"] and "Noted." not in sent[0][2]["text"]
    assert sent[0][2]["session_tag"] == "oc-test" and sent[0][3]["x-statejar-client"] == "openclaw"
    assert "INR 80000" in out["search"]["content"][0]["text"] and out["search"]["details"]["handle"].startswith("shm_")


def test_a_one_shot_run_sends_its_message_at_the_end_of_the_turn(api, tmp_path):
    env, _ = api
    node(tmp_path, """
await fire("agent_end", { success: true, messages: [{ role: "user", content: [{ type: "text", text: "I work as a nurse in Pune." }] }] })
console.log("{}")
""", openclaw_env(env, tmp_path))
    (sent,) = [c for c in API.calls if c[1].endswith("/memory/ingest")]          # no gateway: nothing later would send it
    assert "nurse" in sent[2]["text"]


def test_options_turn_recall_and_capture_off(api, tmp_path):
    env, _ = api
    API.memory = "Known user state:\nfacts.city: Pune"
    out = node(tmp_path, """
const recalled = await fire("before_prompt_build", { prompt: "x", currentUserMessage: "Where do I live?", messages: [] })
await fire("agent_end", { success: true, messages: [{ role: "user", content: "I work as a nurse in Pune." }] })
console.log(JSON.stringify({ recalled: recalled ?? null }))
""", openclaw_env(env, tmp_path, autoRecall=False, autoCapture=False))
    assert out["recalled"] is None and API.calls == []


def test_the_openclaw_scrubber_removes_what_the_python_one_removes(api, tmp_path):
    env, _ = api
    out = node(tmp_path, f"""
const said = {json.dumps(CORPUS)}
for (const text of said) await fire("agent_end", {{ success: true, messages: [{{ role: "user", content: text }}] }})
console.log("{{}}")
""", openclaw_env(env, tmp_path, scope="oc-scrub"))
    scrub = python_scrub()
    texts = [c[2]["text"] for c in API.calls if c[1].endswith("/memory/ingest")]
    expected = [scrub(t)[0] for t in CORPUS if len(t.split()) >= 3]
    assert texts == expected
