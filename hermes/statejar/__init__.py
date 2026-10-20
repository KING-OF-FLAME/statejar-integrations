"""StateJar memory provider for Hermes Agent (memory.provider: statejar). Python 3.10+, standard library only.

- prefetch(query): before each model call, the memory this message needs (the whole state while it is small, the
  fields a question needs once it is large), with the handle (shm_...) it came from. Each recall is an audited
  disclosure in the user's StateJar account.
- sync_turn(user, assistant): the user's message is queued on this machine with secrets removed (never the
  assistant's reply, never tool output), and sent in batches: three messages, or the oldest ten minutes old, or at
  compression, session end and shutdown. StateJar extracts typed facts from them and stores no transcript.
- on_memory_write: what Hermes's built-in memory tool saves is queued the same way.
- Tools: statejar_search, statejar_remember, statejar_forget, statejar_list.
- `hermes statejar status|search|add|list|forget|export` (cli.py).

Writes happen only for the primary agent: cron, subagent and flush contexts read but never capture.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

try:                                            # Hermes 0.19: threads that keep the profile's context
    from agent.memory_provider import spawn_context_thread
except ImportError:                             # pragma: no cover - older Hermes
    import threading

    def spawn_context_thread(target, name=None):
        return threading.Thread(target=target, name=name, daemon=True)

try:
    from agent.secret_scope import get_secret
except ImportError:                             # pragma: no cover - older Hermes
    def get_secret(name: str) -> Optional[str]:
        return os.environ.get(name)

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
DEFAULT_API = "https://api.statejar.com/api/v1"
DEFAULT_SCOPE = "hermes"
BATCH_SIZE = 3
BATCH_AGE_S = 600
MAX_BATCH_CHARS = 18_000
MAX_CONTEXT_CHARS = 9_000
SESSION_QUERY = "Who is the user, what do they prefer, and what are they working on?"
KEY_ENV = "STATEJAR_API_KEY"

# --- secrets out before anything is queued (the same patterns as the Claude Code / Codex hook and the server) ------

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
    """Every pattern against the original text, the spans replaced in one pass."""
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


def _credentials_key() -> str:
    """The key `statejar login` keeps, when no STATEJAR_API_KEY is set."""
    override = os.environ.get("STATEJAR_CONFIG_DIR")
    if override:
        path = Path(override) / "credentials.json"
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "statejar" / "credentials.json"
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "statejar" / "credentials.json"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("api_key") or "").strip()
    except (OSError, ValueError):
        return ""


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class StateJarMemoryProvider(MemoryProvider):
    """One instance per profile: every path and the key are bound in initialize()."""

    def __init__(self) -> None:
        self._key = ""
        self._api = DEFAULT_API
        self._scope = DEFAULT_SCOPE
        self._dir: Optional[Path] = None
        self._writes = True
        self._capture = True
        self._recall_each = True
        self._session_memory = ""
        self._thread = None

    # -- identity and setup ------------------------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "statejar"

    def is_available(self) -> bool:
        return bool((get_secret(KEY_ENV) or "").strip() or _credentials_key())

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "api_key", "description": "StateJar developer key (sj_live_...)", "secret": True, "required": True,
             "env_var": KEY_ENV, "url": "https://statejar.com/api-keys"},
            {"key": "scope", "description": "Memory scope (session) in your StateJar account", "default": DEFAULT_SCOPE},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home) / "statejar.json"
        current = self._read_config(Path(hermes_home))
        current.update({k: v for k, v in values.items() if k != "api_key"})
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")

    @staticmethod
    def _read_config(home: Path) -> Dict[str, Any]:
        try:
            return json.loads((home / "statejar.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def initialize(self, session_id: str, **kwargs) -> None:
        home = Path(kwargs.get("hermes_home") or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        config = self._read_config(home)
        self._key = (get_secret(KEY_ENV) or "").strip() or _credentials_key()
        self._api = str(config.get("api_url") or os.environ.get("STATEJAR_API_URL") or DEFAULT_API).rstrip("/")
        scope = str(config.get("scope") or os.environ.get("STATEJAR_SCOPE") or DEFAULT_SCOPE)
        self._scope = scope if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", scope) else DEFAULT_SCOPE
        self._capture = _flag(config.get("auto_capture", os.environ.get("STATEJAR_AUTO_CAPTURE")), True)
        self._recall_each = _flag(config.get("recall_each_prompt", os.environ.get("STATEJAR_RECALL_EACH_PROMPT")), True)
        self._writes = kwargs.get("agent_context", "primary") in ("primary", None, "")
        self._dir = home / "statejar"
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- the API -----------------------------------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 8.0) -> tuple[int, dict]:
        if not self._key:
            return 0, {}
        req = urllib.request.Request(self._api + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-API-Key": self._key, "Content-Type": "application/json",
                                              "User-Agent": f"statejar-hermes/{VERSION}", "X-StateJar-Client": "hermes"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            return -1, {}

    def _recall(self, question: str, timeout: float) -> str:
        status, r = self._call("POST", "/memory/query", {"query": question[:2000], "session_tag": self._scope}, timeout)
        if status != 200 or not r.get("handle_used") or not r.get("memory_context"):
            return ""
        return str(r["memory_context"])[:MAX_CONTEXT_CHARS]

    # -- the queue ---------------------------------------------------------------------------------------------------

    def _queue_file(self) -> Path:
        assert self._dir is not None, "initialize() first"
        return self._dir / "queue.jsonl"

    def _enqueue(self, text: str) -> None:
        clean, _ = scrub(text)
        fd = os.open(self._queue_file(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": int(time.time()), "text": clean[:MAX_BATCH_CHARS]}) + "\n")

    def _queued(self, path: Optional[Path] = None) -> List[dict]:
        try:
            return [json.loads(x) for x in (path or self._queue_file()).read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            return []

    def _state(self) -> dict:
        try:
            return json.loads((self._dir / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def flush(self, force: bool) -> str:
        """Send the queue in batches; anything refused stays queued for the next flush."""
        if self._dir is None or (self._dir / "paused").exists():
            return "paused"
        items = self._queued()
        if not items:
            return "empty"
        if not force and len(items) < BATCH_SIZE and time.time() - items[0]["t"] < BATCH_AGE_S:
            return "waiting"
        sending = self._dir / f"queue.sending.{os.getpid()}.{time.time_ns()}.jsonl"
        try:
            os.replace(self._queue_file(), sending)
        except OSError:
            return "busy"
        items = self._queued(sending)
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
            text = "\n\n".join(x["text"] for x in batch)[:MAX_BATCH_CHARS]
            last, _ = self._call("POST", "/memory/ingest", {"text": text, "session_tag": self._scope, "async": True}, 15)
            if last not in (200, 202):
                left = [x for b in batches[i:] for x in b]
                break
        with open(self._queue_file(), "a", encoding="utf-8") as f:
            for it in left:
                f.write(json.dumps(it) + "\n")
        sending.unlink(missing_ok=True)
        st = self._state()
        st.update(last_flush=int(time.time()), last_status=last, sent=st.get("sent", 0) + len(items) - len(left))
        (self._dir / "state.json").write_text(json.dumps(st), encoding="utf-8")
        return f"sent {len(items) - len(left)}, kept {len(left)} (status {last})"

    def _flush_in_background(self, force: bool) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = spawn_context_thread(lambda: self.flush(force), name="statejar-flush")
        self._thread.start()

    def _paused(self) -> bool:
        return self._dir is not None and (self._dir / "paused").exists()

    # -- the lifecycle Hermes calls ----------------------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._key:
            return ("StateJar memory: no developer key yet (set STATEJAR_API_KEY with `hermes memory setup`, or run "
                    "`statejar login`); nothing is recalled or captured.")
        return (f"StateJar memory scope: {self._scope}. Recalled memory names the handle (shm_...) of the state it "
                "came from; tools statejar_search, statejar_remember, statejar_forget, statejar_list.")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._paused() or not self._key:
            return ""
        if self._recall_each and query.strip():
            recalled = self._recall(query, timeout=4)
            if recalled:
                return recalled
        if not self._session_memory:
            self._session_memory = self._recall(SESSION_QUERY, timeout=8)
        return self._session_memory

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: Optional[List[Dict[str, Any]]] = None) -> None:
        if not (self._writes and self._capture) or self._paused():
            return
        text = (user_content or "").strip()
        if len(text.split()) >= 3 and not text.startswith("/"):
            self._enqueue(text)                  # the user's words only: never the reply, never tool output
        if len(self._queued()) >= BATCH_SIZE:
            self._flush_in_background(force=False)

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if action in ("add", "replace") and content and self._writes and self._capture and not self._paused():
            self._enqueue(content)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if self._writes:
            self.flush(force=True)
        return f"StateJar memory (scope {self._scope}):\n{self._session_memory}" if self._session_memory else ""

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._writes:
            self.flush(force=True)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False,
                          rewound: bool = False, **kwargs) -> None:
        if reset:
            self._session_memory = ""
            if self._writes:
                self._flush_in_background(force=True)

    def shutdown(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._writes and self._dir is not None:
            self.flush(force=True)

    # -- tools -------------------------------------------------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        def tool(name: str, description: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
            return {"name": name, "description": description,
                    "parameters": {"type": "object", "properties": props, "required": required}}
        text = {"type": "string"}
        return [
            tool("statejar_search", "The memory a question needs from the user's StateJar memory, with the handle it "
                 "came from. Use before answering a question that depends on the user's facts.",
                 {"query": {**text, "description": "The user's question."}}, ["query"]),
            tool("statejar_remember", "Store facts the user states, now. StateJar keeps typed facts, refuses a value "
                 "that fails its type, and stores no transcript. Use only when the user asks to remember something.",
                 {"text": {**text, "description": "The user's words."}}, ["text"]),
            tool("statejar_forget", "Remove one fact from the current memory as a new version (history keeps it). "
                 "Use only when the user asks to forget something.",
                 {"fact": {**text, "description": "The fact to remove, e.g. budget."}}, ["fact"]),
            tool("statejar_list", "Every fact in the user's current StateJar memory, with its handle.", {}, []),
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "statejar_search":
            status, r = self._call("POST", "/memory/query", {"query": str(args.get("query", ""))[:2000],
                                                             "session_tag": self._scope, "audit": True})
            out = {"memory_context": r.get("memory_context"), "handle_used": r.get("handle_used")}
        elif tool_name in ("statejar_remember", "statejar_forget"):
            if not self._writes:
                return json.dumps({"error": "this agent context does not write to memory"})
            text = scrub(str(args.get("text", "")))[0] if tool_name == "statejar_remember" \
                else f"Forget the {str(args.get('fact', '')).strip()}."
            status, r = self._call("POST", "/memory/ingest", {"text": text[:MAX_BATCH_CHARS], "session_tag": self._scope}, 60)
            out = {k: r.get(k) for k in ("stored", "handle", "extraction", "confirmations") if k in r}
        elif tool_name == "statejar_list":
            status, r = self._call("POST", "/memory/query", {"query": "Everything you know about me.",
                                                             "session_tag": self._scope, "audit": True})
            out = {"memory_context": r.get("memory_context"), "handle_used": r.get("handle_used")}
        else:
            raise NotImplementedError(f"statejar does not handle {tool_name}")
        if status not in (200, 202):
            return json.dumps({"error": f"StateJar answered HTTP {status}", "detail": r.get("detail")})
        return json.dumps(out)

    # -- for cli.py --------------------------------------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        st = self._state()
        return {"provider": "statejar", "paused": self._paused(), "scope": self._scope, "api": self._api,
                "key": f"…{self._key[-4:]}" if self._key else "not set", "queued_messages": len(self._queued()),
                "last_flush": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["last_flush"])) if st.get("last_flush") else None,
                "last_status": st.get("last_status"), "messages_sent": st.get("sent", 0)}


def register(ctx) -> None:
    """Called by Hermes's memory plugin discovery: a new provider instance per load."""
    ctx.register_memory_provider(StateJarMemoryProvider())
