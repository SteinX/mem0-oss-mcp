---
name: status
description: Inspect Mem0 OSS connection, local evidence and memory capture state.
---

Run these commands in the current workspace:

```sh
{{CLI}} status --json
{{CLI}} doctor --json
```

Report actual user/repository identities, `auto_capture`, `durable_writes`,
pause state, local counts and doctor
results. `api_key_configured` refers to the selected OSS MCP credential. A
successful scoped doctor search verifies the bridge connection; authentication
errors mean memory is unavailable, not that there are no memories. Credentials
are configured by regenerating this plugin with `--token-stdin` or `--env-file`.

Automatic extraction is off unless installed with `--auto-capture` or enabled
using `MEM0_CODE_AUTO_FLUSH=true`. Pause/resume controls the shared local runtime;
resume does not independently enable automatic extraction. Explicit MCP tools
remain available while hooks are paused.
