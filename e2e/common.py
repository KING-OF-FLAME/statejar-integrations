"""Shared by the end-to-end runs: a StateJar API client, a throwaway account (deleted at the end), the scripted
stand-in model, and the PASS/FAIL report. Standard library only; never prints a password or a key."""

from __future__ import annotations

import json
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

REPO = Path(__file__).resolve().parents[1]
FACTS = "My name is Meera, I live in Pune and my laptop budget is 65000 rupees."
QUESTION = "What is my laptop budget?"
PRODUCTION = re.compile(r"^https?://(?:www\.|api\.)?statejar\.com(?::\d+)?(?:/|$)")


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token = ""
        self.key = ""

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["X-API-Key"] = self.key
        elif self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def must(self, method: str, path: str, body: dict | None = None) -> dict:
        status, data = self.call(method, path, body)
        if status >= 300:
            raise RuntimeError(f"{method} {path} -> HTTP {status}")
        return data


def refuse_production(*urls: str) -> None:
    if any(PRODUCTION.match(u) for u in urls):
        raise SystemExit("the end-to-end runs use a local or staging StateJar, never production")


@contextmanager
def throwaway_account(api: Api, label: str, checks: list) -> Iterator[str]:
    """Sign up, sign in, make a developer key (yielded); delete the account afterwards, whatever happened."""
    email = f"e2e-{label}-{secrets.token_hex(4)}@example.com"
    password = "E2e-" + secrets.token_urlsafe(18)
    try:
        if api.call("POST", "/auth/signup", {"email": email, "password": password})[0] >= 300:
            raise RuntimeError("signup refused")
        api.token = api.must("POST", "/auth/login", {"email": email, "password": password})["access_token"]
        api.key = api.must("POST", "/apikeys/generate", {"label": f"{label} e2e"})["api_key"]
        yield api.key
    finally:
        if api.token:
            api.key = ""
            status, _ = api.call("DELETE", "/account", {"confirm_email": email, "password": password})
            checks.append(("the throwaway account was deleted", status == 200, f"HTTP {status}"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def scripted_model(log: Path) -> Iterator[int]:
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(REPO / "e2e" / "scripted_model.py"), "--port", str(port), "--log",
                             str(log)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        yield port
    finally:
        proc.terminate()


def wait_for_version(api: Api, scope: str, limit_s: float = 180.0) -> list[str]:
    """The scope's handle chain, oldest first, once it has a version (the batch is sent at the end of session 1)."""
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        versions = api.must("GET", f"/memory/versions?session_tag={scope}").get("versions") or []
        if versions:
            return versions
        time.sleep(2)
    return []


def stored_check(api: Api, scope: str) -> tuple[tuple[str, bool, str], str]:
    versions = wait_for_version(api, scope)
    handle = versions[-1] if versions else "shm_none"
    state = api.must("GET", f"/memory/state/{handle}")["state"] if versions else {}
    ok = bool(versions) and all(x in json.dumps(state) for x in ("Meera", "Pune", "65000"))
    detail = f"{len(versions)} version(s), handle {handle[:16]}…" if versions else "no version"
    return ("session 1: the facts were captured and stored", ok, detail), handle


def report(checks: list, out: Path, meta: dict) -> int:
    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    (out / "summary.json").write_text(json.dumps({**meta, "checks": [
        {"check": c[0], "pass": c[1], "detail": c[2]} for c in checks]}, indent=1))
    return 0 if checks and all(c[1] for c in checks) else 1
