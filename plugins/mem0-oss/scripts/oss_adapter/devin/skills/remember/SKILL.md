---
name: remember
description: Save an explicit fact to the self-hosted Mem0 OSS memory store.
---

Run `{{CLI}} status --json` in the current workspace. Use only this plugin's
`{{SERVER_NAME}}` MCP server. Call `mcp__{{SERVER_NAME}}__add_memory` with
the full fact as `text`, the returned `user_id` and `app_id` as top-level
arguments, and `infer: false` to save it verbatim. Include relevant metadata.

Read the returned `event_id` using `mcp__{{SERVER_NAME}}__get_event_status`. Report storage only if
the status is `SUCCEEDED`; report pending or failed results accurately. Return
the actual memory ID when the event supplies one. Do not promise that repeating
the fact in the conversation will save it: automatic extraction is optional.
