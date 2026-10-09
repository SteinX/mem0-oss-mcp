---
name: remember
description: Save an explicit remember request to the self-hosted OSS MCP bridge.
disable-model-invocation: true
---

Resolve the current `user_id` and `app_id` from session context, or the
generated plugin's `core/memory_cli.py status --json` in the current workspace.
Call `add_memory` with the complete fact as `text`, both identities as
top-level arguments, and `infer: false` for a verbatim save. Include relevant
metadata such as `type`, `source`, and affected files.

Read the returned `event_id` with `get_event_status`. Confirm storage only
when its status is `SUCCEEDED`; report a pending or failed result accurately.
The automatic session extractor continues to capture session evidence at
its normal lifecycle boundaries.
