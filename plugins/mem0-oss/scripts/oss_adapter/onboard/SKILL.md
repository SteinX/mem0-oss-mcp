---
name: onboard
description: Verifies a generated Mem0 OSS Codex plugin, MCP tools, and hook setup.
---

# Mem0 OSS Onboarding

Use this when the user asks to set up or verify Mem0 OSS in Codex.

1. Confirm MCP tools are visible. Search tool names for `mem0` and verify `add_memory`, `search_memories`, and `get_memories` are exposed.
2. If tools are missing, explain that the token env var named by the generated plugin must be present in the Codex process environment, then start a new thread.
3. Run a read check with `search_memories` using `query="health check"` and filters for the active `user_id` and `app_id`.
4. Run a write check only with user approval: add a short `health_check` memory, read event status if available, then delete the probe memory if the bridge returns an id.
5. Confirm hooks only if `~/.codex/hooks.json` contains commands for this generated plugin and `[features].codex_hooks = true` is set.

Never print token values. For generated Mem0 OSS plugins, the token is a local bridge bearer token, not a Mem0 Platform API key.
