# Project Instructions

This project is a small MCP bridge for the self-hosted Mem0 OSS REST server.

- Keep it dependency-light. Prefer the Python standard library unless a real protocol gap requires a package.
- Do not hardcode Mem0 server hosts, ports, API keys, or Codex tokens. Use environment variables.
- Treat `MEM0_OSS_API_KEY`, `MEM0_OSS_MCP_TOKEN`, and real `.env` files as secrets.
- Preserve the public MCP tool names expected by Mem0 clients: `add_memory`, `search_memories`, `get_memories`, `get_memory`, `update_memory`, `delete_memory`, `delete_all_memories`, `delete_entities`, `list_entities`, `list_events`, `get_event_status`.
- Keep destructive operations scoped. `delete_all_memories` must require an explicit user, agent, run, or app scope.
- Add one focused runnable test for any non-trivial mapping or protocol change.
