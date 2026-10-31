# Official plugin compatibility: supported behavior and remaining gaps

The OSS overlay is **not complete parity with the hosted Mem0 plugins**. It
reuses upstream client behavior while translating hosted requests into MCP and
Sidecar calls. An accepted request does not prove equivalent extraction behavior.
This comparison uses the repository's pinned `third_party/mem0` source; it is not
a claim about every upstream host or the latest hosted service implementation.

## Bridge corrections

| Behavior | Sidecar-backed bridge | Direct Core bridge |
| --- | --- |
| Event listing after bridge restart | Queries persisted Sidecar events, not the bridge cache | Process-local only |
| Event visibility | Authenticated keys see their own MCP channel; filtering happens before pagination/counting | Existing local credential filtering |
| Event filters | Optional app, user, agent, run, operation and statuses | Explicit filters rejected as unsupported |
| Entity listing app | Uses explicit `app_id`, falling back to configured app | Existing Core behavior |
| Pi reranking | `rerank: true/false` is forwarded, rather than silently dropped | Same forwarding |

`list_events` retains the `results`, `count`, `page`, and `page_size` envelope
and adds `has_more` in Sidecar mode. It searches the configured project across
apps unless `app_id` is supplied, matching durable event-status lookup.
Operations accepted as filters are `ADD`, `GET_ALL`, and `SEARCH`.
Sidecar's query API bounds the visible window to the most recent 5000 matching
events; `count` is not an unbounded historical total. Event results are bounded
previews, not a complete memory export.

The bridge uses its private operator credential for event reads, applies a
server-derived credential channel filter, and checks returned event ownership.
Client-supplied channel/project identifiers cannot broaden this query. This
does not grant the caller operator access to other Sidecar APIs. The legacy
shared MCP token remains a shared identity; disabled authentication/stdio do
not provide per-key event isolation.

## Optional request profiles and dual extraction

| Behavior | OSS compatibility boundary |
| --- | --- |
| Request-local instructions/categories | Pi, OpenCode, and the generic client forward bounded request fields; Sidecar honors them only when operator policy allows | This is not global project configuration or hosted model-quality parity |
| Project configuration | `getProject` and `updateProject` fail explicitly; Python setup is a no-op | Operator-only Sidecar policy remains authoritative |
| Dual extraction | Opt in with `MEM0_OSS_EXTRACTION_MODE=dual` or an explicit request mode | Requires `infer=true`, explicit user and agent IDs, a stable key, and Sidecar `request-profiles-v1` with dual enabled |
| Authenticated entity administration | Explicit app is mapped correctly, but Sidecar entity routes remain operator-only | MCP caller attribution can cause an authorization rejection; this change does not bypass that boundary |
| Host lifecycle behavior | Generated from the pinned supported host overlays | Not all official hosts have an OSS overlay, and no live host/LLM quality test is claimed here |

The default remains single-track. A dual request splits only when `infer` is
exactly `true`; explicit `infer=false`, single-scope hosts, or missing either ID
stay single. No caller identity is guessed from defaults. Both children retain
the same app/project/run attribution, while the project child uses a derived
idempotency key. The composite event ID is
`dual:<personal UUID>:<project UUID>`, remains stable across bridge restarts,
and `list_events` exposes the child rows. The two durable writes are
non-atomic: a second-write failure returns 409 and does not roll back the first.

Before activation, apply compatible Sidecar and Core updates and verify
`GET /v1/extraction-capabilities`. An old or disabled Sidecar is rejected before
any child write. Operator instructions and configured categories win (including
an all-disabled configured set); no global project configuration authority is
granted to callers. This documents a bounded compatibility layer, not full
hosted parity or model quality.

Example request:

```json
{
  "messages": [{"role": "user", "content": "A verified decision"}],
  "user_id": "alice",
  "agent_id": "repository",
  "app_id": "app",
  "run_id": "run",
  "infer": true,
  "extraction_mode": "dual",
  "idempotency_key": "capture-1",
  "custom_instructions": "Keep personal preferences.",
  "agent_custom_instructions": "Keep project procedures.",
  "custom_categories": [{"decision": "Verified decision"}]
}
```

## Regression checks

```bash
python -m pytest -q tests/test_plugin_parity.py tests/test_add_idempotency.py \
  tests/test_caller_context.py tests/test_sitecustomize_adapter.py tests/test_server.py
MEM0_COMPAT_SIDECAR_ROOT=/path/to/mem0-platform-sidecar \
  python -m pytest -q tests/test_plugin_parity.py
tests/run_pi_qa.sh
```

The optional Sidecar contract test requires Sidecar's Python dependencies. It
uses real routes, authentication and a temporary SQLite database, with a seeded
event store; it does not call an LLM or a production service.
