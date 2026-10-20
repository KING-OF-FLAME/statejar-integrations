"""Run inside a Hermes Agent environment (not by pytest directly): load the StateJar provider through Hermes's own
discovery from $HERMES_HOME/plugins/statejar and drive it with Hermes's MemoryManager, as the agent does. Prints one
JSON line with what happened; tests/test_hermes_provider.py checks it against the stand-in API's log."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HERMES_HOME"])
from plugins.memory import discover_memory_providers, load_memory_provider  # noqa: E402  (Hermes's own discovery)
from agent.memory_manager import MemoryManager  # noqa: E402

found = [name for name, *_ in discover_memory_providers()]
provider = load_memory_provider("statejar")
if provider is None:
    print(json.dumps({"found": found, "loaded": False}))
    sys.exit(0)
mgr = MemoryManager()
mgr.add_provider(provider)
mgr.initialize_all(session_id="hermes-test-1", platform="cli", hermes_home=str(home))
tools = sorted(t["name"] for t in provider.get_tool_schemas())
prefetched = provider.prefetch("Which laptop fits my budget?", session_id="hermes-test-1")
for text in ("I'm Kavya and I live in Bengaluru.", "My budget is 80000 rupees and my token is " + os.environ["TOKEN"],
             "I prefer email for anything official."):
    mgr.sync_all(text, "Noted.")
mgr.flush_pending(timeout=10)                   # Hermes runs sync_turn on its own worker; let those writes land
if getattr(provider, "_thread", None) is not None:
    provider._thread.join(timeout=10)
searched = json.loads(mgr.handle_tool_call("statejar_search", {"query": "What is my budget?"}))
status = provider.status()
mgr.on_session_end([])
mgr.shutdown_all()
print(json.dumps({"found": found, "loaded": True, "name": provider.name, "tools": tools, "prefetched": prefetched,
                  "searched": searched, "status": status, "system": provider.system_prompt_block()}))
