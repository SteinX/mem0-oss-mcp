---
name: mem0-oss
description: Use when the user asks to remember, retrieve, search, list, update, or delete project memories through a self-hosted Mem0 OSS MCP bridge.
---

# Mem0 OSS

Use the Mem0 OSS MCP tools when memory context can help with project continuity,
past decisions, user preferences, or explicit remember/search requests.

Prefer scoped calls:

- For writes, include `user_id` and `app_id` when the user or environment has a known scope.
- For searches and listing, include filters with `user_id` and `app_id` when possible.
- Keep saved memories concise: one decision, preference, bug fix, or task outcome per memory.
- Do not store secrets, tokens, private credentials, or raw `.env` contents.

Common tool mapping:

- Save a fact: `add_memory`
- Search memories: `search_memories`
- List recent memories: `get_memories`
- Update a memory by id: `update_memory`
- Delete one memory by id: `delete_memory`
