---
name: pause
description: Pause StateJar memory for Codex, so nothing is recalled or captured until it is resumed. Use only when the user asks to pause StateJar memory.
---

Run this from the plugin's root folder (three folders above this file):

    python3 scripts/statejar_hook.py pause --client codex

Tell the user StateJar memory is paused. Prompts already queued stay queued until it resumes.
