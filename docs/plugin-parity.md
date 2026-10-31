# Official plugin compatibility: supported behavior and remaining gaps

The OSS overlay is **not complete parity with the hosted Mem0 plugins**. It
reuses upstream client behavior while translating hosted requests into MCP and
Sidecar calls. An accepted request does not prove equivalent extraction behavior.
This comparison uses the repository's pinned `third_party/mem0` source; it is not
a claim about every upstream host or the latest hosted service implementation.

## Bridge corrections

| Behavior | Sidecar-backed bridge | Direct Core bridge |
| --- | --- | --- |
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

## Remaining differences requiring a separate extraction/configuration design

| Official behavior | Current OSS behavior | Why simple forwarding is insufficient |
| --- | --- | --- |
| Shared Python plugin sends `custom_instructions` and `agent_custom_instructions` | Bridge add does not forward these fields | Sidecar has one extraction policy/fact kind per add, rather than hosted personal/project dual extraction |
| Pi sends `customCategories` | Pi adapter declares but does not send the option | Sidecar classification is project policy controlled and disabled by default; Core support is also required |
| OpenCode project category setup | `getProject` returns empty categories; `updateProject` is a no-op | Category mutation is operator-only; ordinary MCP callers must not gain global configuration rights |
| Python hosted category setup | Replaced by a local no-op | It does not configure Sidecar project categories |
| Authenticated entity administration | Explicit app is mapped correctly, but Sidecar entity routes remain operator-only | MCP caller attribution can cause an authorization rejection; this change does not bypass that boundary |
| Host lifecycle behavior | Generated from the pinned supported host overlays | Not all official hosts have an OSS overlay, and no live host/LLM quality test is claimed here |

Do not enable or overwrite project extraction policy merely because an official
plugin initializes. Operator-configured instructions, category TTLs, attribution,
explicit-write behavior, and deletion confirmation must remain authoritative.
Full extraction parity requires deciding whether to retain the current single
track or add independently scoped personal and project extraction, including
durable idempotency/recovery for both writes. It also requires a non-admin
configuration contract (for example, request-local profiles rather than granting
project-wide mutation privileges).

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
