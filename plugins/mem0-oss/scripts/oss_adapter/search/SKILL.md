---
name: search
description: Search earlier memories in the current project through the self-hosted OSS MCP bridge.
argument-hint: "[question] [--top-k number] [--run-id session-id]"
disable-model-invocation: true
---

Use the installed `search_memories` tool. Resolve the current `user_id` and
`app_id` from session context, or run the generated plugin's
`core/memory_cli.py status --json` in the current workspace to read them.

Pass the question as `query` and both identities in `filters`:

```json
{"query":"the user's question","filters":{"user_id":"current user","app_id":"current project"},"top_k":5}
```

Map `--top-k` to `top_k` and `--run-id` to `filters.run_id`. Keep the project
scope concrete and retain any requested metadata filters. Return the actual
search results with their memory IDs. The bridge's tool schema is the source
of truth for additional parameters.
