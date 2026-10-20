"""`hermes statejar <command>`: status, search, add, list, forget, export, pause, resume. The commands appear when
memory.provider is statejar (Hermes shows a provider's CLI only while it is active)."""

from __future__ import annotations

import json
import sys

try:
    from hermes_constants import get_hermes_home
except ImportError:                             # pragma: no cover - older Hermes
    import os
    from pathlib import Path

    def get_hermes_home():
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _provider():
    # Hermes imports cli.py on its own, without the provider package, so the provider comes from Hermes's discovery
    from plugins.memory import load_memory_provider
    provider = load_memory_provider("statejar")
    if provider is None:
        sys.exit("The StateJar memory provider is not installed in this Hermes profile.")
    provider.initialize("cli", hermes_home=str(get_hermes_home()), platform="cli", agent_context="primary")
    return provider


def _print(text: str) -> None:
    try:
        data = json.loads(text)
    except ValueError:
        print(text)
        return
    if isinstance(data, dict) and data.get("memory_context"):
        print(data["memory_context"])
        if data.get("handle_used"):
            print(f"\nhandle: {data['handle_used']}")
    else:
        print(json.dumps(data, indent=1, ensure_ascii=False))


def statejar_command(args) -> None:
    sub = getattr(args, "statejar_command", None)
    provider = _provider()
    if sub == "status":
        print(json.dumps(provider.status(), indent=1, ensure_ascii=False))
    elif sub == "search":
        _print(provider.handle_tool_call("statejar_search", {"query": " ".join(args.query)}))
    elif sub == "add":
        _print(provider.handle_tool_call("statejar_remember", {"text": " ".join(args.text)}))
    elif sub == "list":
        _print(provider.handle_tool_call("statejar_list", {}))
    elif sub == "forget":
        _print(provider.handle_tool_call("statejar_forget", {"fact": " ".join(args.fact)}))
    elif sub == "export":
        status, data = provider._call("GET", f"/memory/versions?session_tag={provider._scope}")
        handles = data.get("versions") or []
        out = []
        for h in handles:
            s, state = provider._call("GET", f"/memory/state/{h}")
            out.append({"handle": h, "parent_handle": state.get("parent_handle"), "state": state.get("state"),
                        "created_at": state.get("created_at")})
        print(json.dumps({"scope": provider._scope, "versions": out}, indent=1, ensure_ascii=False))
        if status != 200:
            sys.exit(f"StateJar answered HTTP {status}")
    elif sub == "pause":
        (provider._dir / "paused").touch()
        print("StateJar memory paused: nothing is recalled or captured until `hermes statejar resume`.")
    elif sub == "resume":
        (provider._dir / "paused").unlink(missing_ok=True)
        print("StateJar memory resumed.")
    else:
        print("Usage: hermes statejar <status|search|add|list|forget|export|pause|resume>")


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="statejar_command")
    subs.add_parser("status", help="Scope, what is queued, and the last batch's status (never the key)")
    subs.add_parser("search", help="The memory a question needs, with its handle").add_argument("query", nargs="+")
    subs.add_parser("add", help="Store the facts in a message now").add_argument("text", nargs="+")
    subs.add_parser("list", help="Every fact in the current memory")
    subs.add_parser("forget", help="Remove one fact from the current memory (history keeps it)").add_argument("fact", nargs="+")
    subs.add_parser("export", help="Every version of the scope as JSON, oldest first, with its handle")
    subs.add_parser("pause", help="Stop recall and capture until resume")
    subs.add_parser("resume", help="Turn recall and capture back on")
    subparser.set_defaults(func=statejar_command)
