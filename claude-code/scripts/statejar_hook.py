#!/usr/bin/env python3
"""StateJar for Claude Code: the plugin's hooks and its local commands. Python 3.8+, standard library only.

Hooks (hooks/hooks.json), each reading Claude Code's JSON on stdin:
  session-start   recall: the memory a new session needs, as context for Claude (minimal disclosure: the whole state
                  while it is small, the fields a question needs once it is large; each recall is an audited disclosure)
  prompt          capture: the user's prompt, secrets removed, queued on this machine (no network); then, if
                  recall_each_prompt is on, the memory this prompt needs as context
  stop            flush the queue in one batch when it is long or old enough (run in the background)
  pre-compact     flush now, in a detached process
  session-end     flush now, in a detached process (Claude Code gives SessionEnd hooks 1.5 s)
Local commands (the plugin's skills call them):
  status | pause | resume | scope | flush

What leaves this machine, and when: a recall sends the question (the user's prompt) to StateJar's query endpoint; a
flush sends the queued prompts, joined, to the ingest endpoint, where StateJar extracts typed facts and stores no
transcript. Secrets are removed before a prompt is queued. Nothing here logs or prints a prompt, a fact or a key;
state.json keeps counts, times and the last status code only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API = "https://api.statejar.com/api/v1"
DEFAULT_SCOPE = "claude-code"
BATCH_SIZE = 3                 # prompts per flush at a Stop
BATCH_AGE_S = 600              # or when the oldest queued prompt is this old
MAX_BATCH_CHARS = 18_000       # one ingest carries at most this much (the MCP limit is 20,000)
MAX_CONTEXT_CHARS = 9_000      # Claude Code caps a hook's context at 10,000 characters
SESSION_QUERY = "Who is the user, what do they prefer, and what are they working on?"


# --- configuration ------------------------------------------------------------------------------------------------

def option(name: str, default: str = "") -> str:
    """A plugin option (userConfig), which Claude Code exports to hooks as CLAUDE_PLUGIN_OPTION_<NAME>."""
    return (os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}") or default).strip()


def flag(name: str, default: bool) -> bool:
    value = option(name, "true" if default else "false").lower()
    return value in ("1", "true", "yes", "on")


def credentials_file() -> Path:
    override = os.environ.get("STATEJAR_CONFIG_DIR")
    if override:
        return Path(override) / "credentials.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "statejar" / "credentials.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "statejar" / "credentials.json"


def api_key() -> str:
    """The plugin's own option first, then STATEJAR_API_KEY, then a key `statejar login` kept in its file."""
    key = option("api_key") or os.environ.get("STATEJAR_API_KEY", "").strip()
    if key:
        return key
    try:
        data = json.loads(credentials_file().read_text(encoding="utf-8"))
        return str(data.get("api_key") or "").strip()
    except (OSError, ValueError):
        return ""


def api_url() -> str:
    return (option("api_url") or os.environ.get("STATEJAR_BASE_URL") or DEFAULT_API).rstrip("/")


def scope() -> str:
    value = option("scope") or DEFAULT_SCOPE
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value) else DEFAULT_SCOPE


def data_dir(given: str | None = None) -> Path:
    path = Path(given or os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".statejar-claude-code")
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- secrets out before anything is queued --------------------------------------------------------------------------

SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S)),
    ("api key", re.compile(r"\bsj_live_[A-Za-z0-9_-]{16,}")),
    ("api key", re.compile(r"\bsk-(?:proj-|ant-(?:api\d{2}-)?|or-v1-)?[A-Za-z0-9_-]{20,}")),
    ("api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api key", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("api key", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("api key", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("api key", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("api key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("api key", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("api key", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("api key", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}\b")),
    ("cloud key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("password", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@")),
    ("password", re.compile(
        r"(?i)\b(?:password|passwd|passcode|pass\s*code|pin|otp|cvv|cvc|secret|api[ _-]?key|access[ _-]?token|"
        r"auth[ _-]?token|private[ _-]?key|client[ _-]?secret)\b(?:\s+(?:is|was|=|:))?\s*[:=]?\s*[\"']?"
        r"(?=[^\s\"']*[A-Za-z0-9])([^\s\"',;]{4,})")),
]
CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn(digits: str) -> bool:
    total, odd = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if odd:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        odd = not odd
    return total % 10 == 0


def scrub(text: str) -> tuple[str, int]:
    """Every pattern against the original text, the spans replaced in one pass (the server's scrub.py does the same)."""
    spans = []
    for kind, rx in SECRET_PATTERNS:
        for m in rx.finditer(text):
            spans.append((m.start(1), m.end(1), kind) if rx.groups else (m.start(), m.end(), kind))
    for m in CARD.finditer(text):
        if _luhn(re.sub(r"\D", "", m.group(0))):
            spans.append((m.start(), m.end(), "card number"))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, pos, n = [], 0, 0
    for start, end, kind in spans:
        if start < pos:
            continue
        out += [text[pos:start], f"[secret: {kind}]"]
        pos, n = end, n + 1
    return "".join(out) + text[pos:], n


# --- the API --------------------------------------------------------------------------------------------------------

def call(method: str, path: str, body: dict | None = None, timeout: float = 8.0) -> tuple[int, dict]:
    key = api_key()
    if not key:
        return 0, {}
    req = urllib.request.Request(api_url() + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-API-Key": key, "Content-Type": "application/json",
                                          "User-Agent": "statejar-claude-code/0.1.0", "X-StateJar-Client": "claude-code"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return -1, {}


def recall(question: str, timeout: float) -> str:
    status, r = call("POST", "/memory/query", {"query": question[:2000], "session_tag": scope()}, timeout)
    if status != 200 or not r.get("handle_used") or not r.get("memory_context"):
        return ""
    return str(r["memory_context"])[:MAX_CONTEXT_CHARS]


# --- the local queue and state --------------------------------------------------------------------------------------

def read_state(d: Path) -> dict:
    try:
        return json.loads((d / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(d: Path, **changes: object) -> None:
    state = {**read_state(d), **changes}
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, d / "state.json")


def paused(d: Path) -> bool:
    return (d / "paused").exists()


def queue_lines(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def enqueue(d: Path, text: str) -> None:
    path = d / "queue.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": int(time.time()), "text": text}) + "\n")


def flush(d: Path, force: bool) -> str:
    path = d / "queue.jsonl"
    items = queue_lines(path)
    if not items:
        return "empty"
    if not force and len(items) < BATCH_SIZE and time.time() - items[0]["t"] < BATCH_AGE_S:
        return "waiting"
    sending = d / f"queue.sending.{os.getpid()}.jsonl"
    try:
        os.replace(path, sending)                  # new prompts go to a fresh queue while this batch is sent
    except OSError:
        return "busy"
    items = queue_lines(sending)
    batches, cur = [], []
    for it in items:
        if cur and sum(len(x["text"]) + 2 for x in cur) + len(it["text"]) > MAX_BATCH_CHARS:
            batches.append(cur)
            cur = []
        cur.append(it)
    if cur:
        batches.append(cur)
    left, last = [], 0
    for i, batch in enumerate(batches):
        status, _ = call("POST", "/memory/ingest", {"text": "\n\n".join(x["text"] for x in batch)[:MAX_BATCH_CHARS],
                                                    "session_tag": scope(), "async": True}, timeout=15)
        last = status
        if status not in (200, 202):
            left = [x for b in batches[i:] for x in b]
            break
    for it in left:                                # kept for the next flush: a 429, the network, the key
        enqueue(d, it["text"])
    sending.unlink(missing_ok=True)
    sent = len(items) - len(left)
    write_state(d, last_flush=int(time.time()), last_status=last, sent=read_state(d).get("sent", 0) + sent)
    return f"sent {sent}, kept {len(left)} (status {last})"


def detached_flush(d: Path) -> None:
    args = [sys.executable, os.path.abspath(__file__), "flush", "--force", "--data", str(d)]
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200       # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)                           # noqa: S603 - our own script, fixed arguments


# --- hook output ----------------------------------------------------------------------------------------------------

def context(event: str, text: str) -> None:
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def hook_input() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    given = argv[argv.index("--data") + 1] if "--data" in argv else None
    d = data_dir(given)
    if cmd == "session-start":
        hook_input()
        if paused(d):
            context("SessionStart", "StateJar memory is paused (/statejar:resume turns it back on).")
            return 0
        if not api_key():
            context("SessionStart", "StateJar: no developer key yet. Set it with /plugin (StateJar options) or "
                                    "`statejar login`; until then nothing is recalled or captured.")
            return 0
        memory = recall(SESSION_QUERY, timeout=8)
        note = (f"StateJar memory scope for this session: {scope()}. Its tools (search, remember, get, list, update, "
                f"delete, history, audit, verify, export) take session=\"{scope()}\".")
        context("SessionStart", note + ("\n\n" + memory if memory else ""))
    elif cmd == "prompt":
        data = hook_input()
        prompt = str(data.get("prompt") or "")
        if paused(d) or not prompt.strip() or prompt.lstrip().startswith("/"):
            return 0
        if flag("auto_capture", True) and len(prompt.split()) >= 3:
            clean, _ = scrub(prompt)
            enqueue(d, clean[:MAX_BATCH_CHARS])
        if flag("recall_each_prompt", True) and api_key():
            context("UserPromptSubmit", recall(prompt, timeout=4))
    elif cmd == "stop":
        hook_input()
        if not paused(d):
            flush(d, force=False)
    elif cmd in ("pre-compact", "session-end"):
        hook_input()
        if not paused(d) and queue_lines(d / "queue.jsonl"):
            detached_flush(d)
    elif cmd == "flush":
        print(flush(d, force="--force" in argv))
    elif cmd == "pause":
        (d / "paused").touch()
        print("StateJar memory paused: nothing is recalled or captured until /statejar:resume.")
    elif cmd == "resume":
        (d / "paused").unlink(missing_ok=True)
        print("StateJar memory resumed.")
    elif cmd == "scope":
        print(scope())
    elif cmd == "status":
        st = read_state(d)
        key = api_key()
        print(json.dumps({
            "paused": paused(d), "scope": scope(), "api": api_url(),
            "key": f"…{key[-4:]}" if key else "not set (inline commands do not see the plugin's key)",
            "queued_prompts": len(queue_lines(d / "queue.jsonl")),
            "last_flush": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["last_flush"])) if st.get("last_flush") else None,
            "last_status": st.get("last_status"), "prompts_sent": st.get("sent", 0),
        }, indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # a hook must never break the session; say only the kind of error
        print(f"statejar hook: {type(exc).__name__}", file=sys.stderr)
        sys.exit(0)
