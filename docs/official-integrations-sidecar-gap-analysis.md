# Official Mem0 Integrations And Sidecar Gap Analysis

Last checked: 2026-07-03.

Sources used:
- Local upstream fetch: `third_party/mem0` `origin/main` at `59484f066f9ba3ef27516390ad45563fe615fb43`.
- Local checkout remains at `41c8f00851a64073953fd9ffbae17cece640bee9`; do not treat checked-out files alone as current official state.
- Mem0 docs pages for Codex, OpenCode, OpenClaw, Hermes Agent, Pi Agent, and Mem0 MCP.

## Executive Summary

The official integrations fall into two groups:

1. Thin MCP or SDK tool surfaces that expose memory CRUD/search to agents.
2. Agent-specific automation layers that decide when to recall, capture, scope, consolidate, or inspect memory.

The sidecar should not reimplement client automation. It should provide the server-side semantics those clients expect from Mem0 Platform while forwarding core memory operations to Mem0 OSS.

The highest-value sidecar gaps are:

1. Durable async events for memory operations.
2. Project/account category configuration and category filtering.
3. Platform-compatible endpoint and SDK behavior for tools that call `api.mem0.ai`.
4. Robust scope semantics for `user_id`, `agent_id`, `app_id`, and `run_id`.
5. Entity listing/deletion parity.
6. Memory export/import and inspection APIs for skills and dashboards.
7. Optional webhook/job/analytics surfaces after the above are solid.

## Integration Capability Matrix

| Integration | Official packaging | Backend mode | Tools | Automation | Scope model | Platform assumptions that matter to sidecar |
| --- | --- | --- | --- | --- | --- | --- |
| Codex | `integrations/mem0-plugin` sideloaded plugin plus opt-in Codex hooks; direct MCP is also documented | Mem0 Platform MCP/API | 9 MCP tools in docs; current hosted MCP docs also include `list_events` and `get_event_status` | Session start, user prompt retrieval, pre-tool metadata enforcement, file-read memory context, bash-error lookup, stop/pre-compact summaries | `user_id` plus `app_id`; hooks derive user/project/branch and enforce metadata defaults | Hosted MCP, Platform memory API, `project.update(custom_categories)`, async events, entity APIs |
| Claude Code / Claude Cowork / Cursor / Antigravity | Same `mem0-plugin` family with client-specific install/hook behavior | Mem0 Platform MCP/API | Same MCP memory tool family | Similar lifecycle hooks where supported; Cursor MCP-only is lighter | Same identity/project derivation style | Same as Codex, with hook availability varying by client |
| OpenCode | Native TypeScript plugin `@mem0/opencode-plugin`; no MCP needed for full install | Mem0 Platform SDK/API | Native tools: add/search/get/list/update/delete/delete_all/entities/event status | Pure TS hooks: command registration, prompt recall, auto-capture, bash-error lookup, compaction preservation, shell env export, auto-dream | `project`, `session`, `global`; default stored in `~/.mem0/settings.json`; project id from git remote/root | SDK project APIs for custom categories, `/v1/event/{id}/`, entity APIs, wildcard/global scope behavior |
| OpenClaw | `@mem0/openclaw-mem0` memory slot plugin | Platform or open-source SDK mode | `memory_search`, `memory_add`, `memory_get`, `memory_list`, `memory_update`, `memory_delete`, `memory_event_list`, `memory_event_status` | Skills mode by default: triage, recall, dream; also auto-recall/auto-capture | Session vs long-term; multi-agent namespace via agent session routing | Platform-only events/entities; OSS mode explicitly lacks event/entity management and metadata-only updates |
| Hermes Agent | Hermes memory provider named `mem0`, documented by Mem0 and Hermes | Platform or OSS provider mode | `mem0_list`, `mem0_search`, `mem0_add`, `mem0_update`, `mem0_delete` | Background sync after response, background prefetch for next turn, zero-latency cached recall, circuit breaker | `user_id`, `agent_id`; additive to built-in `MEMORY.md`/`USER.md`; one external provider active | Less dependent on our Codex/OpenCode bridge, but still benefits from Platform-compatible behavior if pointed at sidecar |
| Pi Agent | `@mem0/pi-agent-plugin` | Mem0 Platform SDK/API | One `mem0_memory` tool with actions: search, add, get_all, update, delete, delete_all | Auto-capture, semantic recall, system prompt policy, dream consolidation, confirmation dialogs | `project`, `session`, `global`; project scope is git-root aware | SDK `customCategories` on add, search threshold/rerank behavior, stable category metadata |
| Standalone Mem0 MCP | Hosted MCP server | Mem0 Platform MCP | `add_memory`, `search_memories`, `get_memories`, `get_memory`, `update_memory`, `delete_memory`, `delete_all_memories`, `delete_entities`, `list_entities`, `list_events`, `get_event_status` | None beyond what the client decides | Tool arguments only | This is the minimum public protocol a sidecar should emulate |

## Cross-Cutting Capabilities

### 1. Memory CRUD And Search

Every integration depends on:

- Add memory from text or message arrays.
- Semantic search.
- List/get/update/delete memory by ID.
- Bulk deletion scoped to user/agent/app/run.
- Filters over `user_id`, `agent_id`, `app_id`, `run_id`, metadata, and categories.

Mem0 OSS already provides most of the data-plane behavior. The sidecar should normalize the Platform-style request shapes and translate them into OSS REST calls.

### 2. Identity And Scope

Official plugins rely on predictable scoping:

- `user_id`: user namespace.
- `agent_id`: subagent or agent namespace.
- `app_id`: project/repository namespace.
- `run_id`: session namespace.

Codex and OpenCode are especially sensitive to project isolation. The sidecar should preserve `app_id` as a first-class field even if OSS stores it in metadata today.

### 3. Categories

This is the biggest current gap for the OSS bridge.

Official behavior includes:

- Project-level custom category taxonomy via `project.update(custom_categories=...)`.
- Auto-tagging new memories into categories.
- Search/list filters by category.
- Skills and tours that display memories grouped by category.
- Pi/OpenClaw-style per-add category metadata for agentic triage flows.

Mem0 OSS can store metadata, but it does not expose the hosted Platform project/category control plane. The sidecar should own this.

### 4. Events

Official MCP and OpenClaw expose operation event inspection:

- `list_events`
- `get_event_status`
- `/v1/events/`
- `/v1/event/{event_id}/`

The current bridge returns in-memory `SUCCEEDED` events after synchronous writes. A real sidecar should persist events and treat every mutating operation as an evented operation, even if OSS completes synchronously underneath.

### 5. Automation And Consolidation

Auto-capture, prefetch, resume recovery, dream consolidation, and memory triage are mostly client/plugin behavior. The sidecar does not need to decide when an agent should remember something.

The sidecar does need to make these behaviors reliable:

- Provide enough filters/list APIs for dream workflows.
- Preserve IDs through update flows where possible.
- Support metadata/category updates for rewrite and merge workflows.
- Support safe destructive operations with clear scope.

### 6. Entity APIs

Hosted Mem0 exposes entity listing/deletion around users, agents, apps, and runs. Several integrations expose these tools directly.

Mem0 OSS has limited entity support. The sidecar can maintain an entity index derived from memory operations, then forward destructive operations to OSS memory deletion paths.

## Sidecar Requirements

### Phase 1: Protocol Parity For Existing Plugins

Goal: make official Codex/OpenCode-style integrations work against OSS without patching the integrations deeply.

Required:

- MCP `tools/list` and `tools/call` parity for the hosted tool names.
- Platform endpoint adapter for:
  - `POST /v3/memories/add`
  - `POST /v3/memories/search`
  - `GET/POST /v3/memories`
  - `GET/PUT/PATCH/DELETE /v3/memories/{id}`
  - `GET /v1/events/`
  - `GET /v1/event/{id}/`
- Durable event table with statuses such as `PENDING`, `SUCCEEDED`, `FAILED`.
- Scope normalization and metadata flattening.
- Expiration-date preservation and filtering.
- Overfetch plus post-filter behavior for filters OSS cannot push down cleanly.

This is close to the current `mem0-oss-mcp`, but events must move out of process memory.

### Phase 2: Category Control Plane

Goal: replace the hosted `project.update(custom_categories)` dependency.

Required:

- Project table keyed by `app_id` or API-key/account scope.
- Category taxonomy table with version/fingerprint.
- Endpoints or SDK-compatible mappings for:
  - project get fields `custom_categories` / `customCategories`
  - project update with full taxonomy replacement
- Add-time categorization strategy:
  - Fast path: trust explicit `metadata.type`, `metadata.category`, `categories`, or `customCategories`.
  - Optional classifier: map new memory text to configured categories before writing to OSS metadata.
- Search/list filtering by category.
- Backfill job to classify older memories.

This lets the official onboarding/category setup stop being a no-op in OSS mode.

### Phase 3: Entity And Export Management

Goal: make management skills and dashboards behave like Platform.

Required:

- Entity index for users, agents, apps, and runs.
- `list_entities` and `delete_entities` parity.
- Export API/job for project/user scoped memory dumps.
- Import API or bulk add path for markdown/json exports.
- Audit trail for destructive actions.

### Phase 4: Webhooks, Analytics, And Dashboard Support

Goal: product/control-plane completion.

Required:

- Webhook registrations and delivery attempts.
- Event fanout from the durable event table.
- Request/event analytics.
- Dashboard API endpoints for memories, entities, categories, events, exports, and settings.

This should come after categories/events/entities, because those are the data model the dashboard would display.

## Recommended Architecture

Keep Mem0 OSS as the memory engine and make the sidecar the only client-facing memory API:

```text
Agents / plugins / MCP clients
  -> sidecar MCP + Platform-compatible HTTP API
     -> sidecar DB: events, categories, entities, jobs, webhooks
     -> Mem0 OSS REST API: add/search/get/update/delete memories
```

Do not write directly to Mem0 OSS storage tables. Use OSS REST as the data-plane contract so upstream upgrades remain manageable.

## Open Questions

- Should categories be scoped by API key/account, `app_id`, or both? Official plugin comments imply Platform categories are account/project-level, while coding-agent workflows often want project-level taxonomies.
- Should sidecar expose hosted-style API-key semantics (`m0-...`) or keep the current OSS bridge bearer token plus upstream OSS API key split?
- Should classification be deterministic/rule-based first, or use an LLM provider from the OSS configuration?
- Should Hermes point directly at Mem0 OSS in OSS mode, or should we recommend pointing it at the sidecar once Platform parity matters?

## Immediate Next Step

Design Phase 1 and Phase 2 together:

1. Add a sidecar database schema for `events`, `projects`, `categories`, `entities`, and `jobs`.
2. Move current in-memory events into the DB.
3. Add category project get/update compatibility.
4. Add category-aware add/search/list behavior.
5. Keep the official plugin overlays thin and upgrade-safe.
