---
name: forget
description: Remove one fact from the user's current StateJar memory. History keeps it; contact details are not removed by name.
argument-hint: "[fact, e.g. budget]"
disable-model-invocation: true
allowed-tools: mcp__plugin_statejar_statejar__delete
---

Call the StateJar tool `mcp__plugin_statejar_statejar__delete` with fact set to: $ARGUMENTS
and session set to "${user_config.scope}".

Say whether a new version was stored (the fact removed from the current state) and its handle. The earlier versions
keep the fact; say so.
