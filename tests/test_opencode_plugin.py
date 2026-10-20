"""The OpenCode plugin (opencode/statejar.js) run in Node against the stand-in StateJar API: what it sends, when, and
what it keeps on the machine; and its secret scrubbing, which must remove exactly what the Python hook script removes.
The run with the real OpenCode binary, a fact stored in one session and recalled in a fresh one, is e2e/opencode_e2e.py."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess

import pytest

from conftest import API, HOOK, KEY, ROOT, TOKEN

PLUGIN = ROOT / "opencode" / "statejar.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node 20+")


def node(script: str, timeout: int = 60) -> dict:
    """Run an ES module that imports the plugin as `plugin` and prints one JSON line."""
    code = f'import {{ StateJar as plugin }} from {json.dumps(PLUGIN.as_uri())}\n{script}'
    r = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def python_scrub():
    spec = importlib.util.spec_from_file_location("statejar_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scrub


CORPUS = [
    "I'm Kavya and I live in Bengaluru.",
    f"my GitHub token is {TOKEN} for later",
    "key: sk-" + "proj-" + "A" * 30 + " and sj_live_" + "b" * 20,
    "AWS AKIA" + "ABCDEFGHIJKLMNOP" + " in the config",
    "my pass" + "word is hunter2xyz, and the pin = 4321",          # built, so the scanner sees no literal
    "card 4111 1111 1111 1111 expires soon; not a card: 1234 5678 9012 3456",
    "postgres://admin:" + "s3cr3tpw" + "@db.internal/app",
    "Authorization: Bearer " + "abcdefghij0123456789.xyz",
    "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEow\n-----END RSA " + "PRIVATE KEY----- done",
    "a jwt eyJ" + "hbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
    "my budget is 80000 rupees and my phone is 98765 43210",
]


def test_the_javascript_scrubber_removes_what_the_python_one_removes():
    out = node(f"console.log(JSON.stringify({json.dumps(CORPUS)}.map((t) => plugin.scrub(t))))")
    scrub = python_scrub()
    for text, (js_text, js_n) in zip(CORPUS, out):
        assert [js_text, js_n] == list(scrub(text)), text
    assert TOKEN not in json.dumps(out) and "hunter2xyz" not in json.dumps(out)


def test_messages_are_queued_scrubbed_sent_in_a_batch_and_recalled_into_the_system_prompt(api, tmp_path):
    env, _ = api
    url = env["CLAUDE_PLUGIN_OPTION_API_URL"]
    API.memory = "Known user state:\nconstraints.budget.max: INR 80000"
    data = tmp_path / "oc-data"
    out = node(f"""
const hooks = await plugin({{}}, {{ apiKey: {json.dumps(KEY)}, apiUrl: {json.dumps(url)}, dataDir: {json.dumps(str(data))},
                                   scope: "oc-test" }})
const say = (text) => hooks["chat.message"]({{ sessionID: "s1" }}, {{ message: {{}}, parts: [{{ type: "text", text }}] }})
await say("I'm Kavya and I live in Bengaluru.")
await say("My budget is 80000 rupees and my GitHub token is {TOKEN}.")
await hooks.event({{ event: {{ type: "session.idle", properties: {{}} }} }})
const fs = await import("node:fs")
const queuedAfterTwo = fs.readFileSync({json.dumps(str(data / "queue.jsonl"))}, "utf8")
await say("/statejar something")
await say("I prefer email for anything official.")
const system = {{ system: [] }}
await hooks["experimental.chat.system.transform"]({{ sessionID: "s1", model: {{}} }}, system)
await hooks.event({{ event: {{ type: "session.idle", properties: {{}} }} }})
const status = JSON.parse(await hooks.tool.statejar_status.execute({{}}, {{}}))
console.log(JSON.stringify({{ queuedAfterTwo, system: system.system, status }}))
""")
    assert TOKEN not in out["queuedAfterTwo"] and "[secret: api key]" in out["queuedAfterTwo"]
    sent = [c for c in API.calls if c[1].endswith("/memory/ingest")]
    assert len(sent) == 1                                          # one batch: three messages, not the command
    body = sent[0][2]
    assert body["session_tag"] == "oc-test" and body["async"] is True
    assert "Kavya" in body["text"] and "email" in body["text"] and TOKEN not in body["text"] and "/statejar" not in body["text"]
    assert sent[0][3]["x-statejar-client"] == "opencode" and sent[0][3]["x-api-key"] == KEY
    (system,) = out["system"]
    assert 'session="oc-test"' in system and "INR 80000" in system
    queries = [c for c in API.calls if c[1].endswith("/memory/query")]
    assert queries and all(q[2]["session_tag"] == "oc-test" for q in queries)
    assert out["status"]["messages_sent"] == 3 and out["status"]["queued_messages"] == 0
    assert out["status"]["key"] == "…" + KEY[-4:] and out["status"]["last_status"] == 202
    for word in ("Kavya", "Bengaluru", "80000", KEY):
        assert word not in json.dumps(out["status"])
    assert oct((data / "state.json").stat().st_mode)[-3:] in ("600", "644")
    assert "Kavya" not in (data / "state.json").read_text()


def test_dispose_sends_what_is_left_and_a_refused_batch_stays_queued(api, tmp_path):
    env, _ = api
    url = env["CLAUDE_PLUGIN_OPTION_API_URL"]
    API.ingest_status = 429
    data = tmp_path / "oc-data"
    common = f"""
const hooks = await plugin({{}}, {{ apiKey: {json.dumps(KEY)}, apiUrl: {json.dumps(url)}, dataDir: {json.dumps(str(data))} }})
const fs = await import("node:fs")
const lines = () => fs.existsSync({json.dumps(str(data / "queue.jsonl"))}) ?
  fs.readFileSync({json.dumps(str(data / "queue.jsonl"))}, "utf8").split("\\n").filter(Boolean).length : 0
"""
    out = node(common + """
await hooks["chat.message"]({ sessionID: "s" }, { message: {}, parts: [{ type: "text", text: "I work as a nurse in Pune." }] })
await hooks.dispose()
console.log(JSON.stringify({ left: lines() }))
""")
    assert out["left"] == 1                                        # 429: kept for the next flush
    API.ingest_status = 202
    out = node(common + "await hooks.dispose(); console.log(JSON.stringify({ left: lines() }))")
    assert out["left"] == 0
    assert [c[2]["session_tag"] for c in API.calls if c[1].endswith("/memory/ingest")] == ["opencode", "opencode"]


def test_pause_stops_capture_and_recall_and_no_key_means_no_network(api, tmp_path):
    env, _ = api
    url = env["CLAUDE_PLUGIN_OPTION_API_URL"]
    data = tmp_path / "oc-data"
    out = node(f"""
const hooks = await plugin({{}}, {{ apiKey: {json.dumps(KEY)}, apiUrl: {json.dumps(url)}, dataDir: {json.dumps(str(data))} }})
await hooks.tool.statejar_pause.execute({{}}, {{}})
await hooks["chat.message"]({{ sessionID: "s" }}, {{ message: {{}}, parts: [{{ type: "text", text: "I work as a nurse in Pune." }}] }})
const paused = {{ system: [] }}
await hooks["experimental.chat.system.transform"]({{ sessionID: "s", model: {{}} }}, paused)
await hooks.tool.statejar_resume.execute({{}}, {{}})
process.env.STATEJAR_API_KEY = ""
process.env.STATEJAR_CONFIG_DIR = {json.dumps(str(tmp_path / "nocreds"))}
const keyless = await plugin({{}}, {{ apiUrl: {json.dumps(url)}, dataDir: {json.dumps(str(tmp_path / "other"))} }})
const sys = {{ system: [] }}
await keyless["chat.message"]({{ sessionID: "k" }}, {{ message: {{}}, parts: [{{ type: "text", text: "What is my budget for this?" }}] }})
await keyless["experimental.chat.system.transform"]({{ sessionID: "k", model: {{}} }}, sys)
console.log(JSON.stringify({{ paused: paused.system, keyless: sys.system }}))
""")
    assert out["paused"] == [] and not (data / "queue.jsonl").exists()
    assert "no developer key" in out["keyless"][0]
    assert API.calls == []                                         # paused, then keyless: nothing went out


def test_the_module_exports_only_the_plugin_and_the_package_names_it():
    out = node("const m = await import(" + json.dumps(PLUGIN.as_uri()) + "); console.log(JSON.stringify(Object.keys(m)))")
    assert out == ["StateJar"]                                     # OpenCode calls every export as a plugin
    pkg = json.loads((ROOT / "opencode" / "package.json").read_text())
    assert pkg["main"] == "statejar.js" and pkg["type"] == "module" and "dependencies" not in pkg
    assert (ROOT / "opencode" / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
