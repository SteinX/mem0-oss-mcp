---
name: dream
description: Manual trigger only. Use only when the user explicitly invokes `/mem0:dream` or directly asks to run Dream memory maintenance for the active Mem0 OSS project.
---

# Mem0 OSS Dream - Manual Memory Maintenance

Manual trigger only: run this skill only when the user explicitly invokes
`/mem0:dream` or directly asks to run Dream memory maintenance. Do not infer a
Dream run from memory count, noisy search results, elapsed time, routine health
checks, or unrelated memory work. Do not schedule or start it in the background.

Fetch the active project, analyze everything in memory, show the proposed diff,
then apply only after confirmation. Do not perform destructive changes before
the report is shown.

Run steps in order. Do not skip the fetch completeness checks.

## Modes

- Default / manual mode: read all memories, print the diff report, wait for user
  confirmation, then apply approved changes.
- `--dry-run`: read and report only. Never call `add_memory`, `update_memory`,
  or `delete_memory`.

## Step 1: Resolve Scope And Policies

Use the active `user_id` and `app_id` from the environment or current Mem0
instructions. For this plugin, `app_id` is the project scope and must be passed
as top-level `app_id` on writes, not only in metadata.

Load retention policies:

```bash
python3 "<PLUGIN_ROOT>/scripts/parse_mem0_config.py" "<cwd>"
```

If the parser fails or returns `{}`, use these OSS defaults:

| `metadata.type` | Default retention |
|---|---|
| `session_state` | 90 days |
| `compact_summary` | 90 days |
| `debugging_note` with `confidence < 0.5` | 180 days |
| all others | no pruning |

Pinned memories (`metadata.pinned == true`) are never pruned or merged.

## Step 2: Fetch All Project Memories

Call `get_memories` using explicit scope filters:

```python
get_memories(
    filters={"AND": [{"user_id": "<active_user_id>"}, {"app_id": "<active_project_id>"}]},
    page=1,
    page_size=500,
)
```

Paginate until all pages are read. Continue while `page * page_size < count`.
If the response contains `truncated: true` or `complete: false`, stop before
analysis and report:

```text
Dream paused. Memory listing is incomplete at fetch_limit=<N>; raise MEM0_OSS_LIST_FETCH_LIMIT or backend list limit before consolidation.
```

Do not apply merges or prunes from an incomplete listing.

If zero memories are found, print:

```text
Dream complete. No memories found for project <project_id>.
```

## Step 3: Analyze In Memory

Group by `metadata.type`, falling back to `unknown`. Use `metadata.source`,
`metadata.topic`, `metadata.files`, `created_at`, and `updated_at` as supporting
signals.

Find these issues:

1. Exact duplicate hashes: same `hash`, same type, not pinned.
2. Near duplicates: same type/topic or high noun overlap, compatible facts, not
   pinned. Only merge when the merged text is clearly more complete.
3. Contradictions: opposing facts about the same topic. Do not auto-resolve.
4. Prune candidates: expired by policy, or confidence below 0.3 with no unique
   project path, identifier, or decision.

Do not merge across different `metadata.type` values unless one is `unknown` and
the correct type is obvious.

## Step 4: Print Report

Use this format:

```text
## dream - OSS manual report

Scope: user=<user_id> app=<app_id>
Reviewed: <N> memories
Completeness: complete | incomplete fetch_limit=<N>

Merges (<N>):
  [mem0:<id1>] + [mem0:<id2>] -> "<merged content, 120 chars>"

Conflicts (<N>):
  [mem0:<idA>] vs [mem0:<idB>] - "<topic>" [A/B/skip]

Prune (<N>):
  [mem0:<id>] - <type>, <age>d old, reason=<reason>

Proposed: <N> merges, <N> prunes, <N> conflicts. Apply? [Y/n]
```

Omit empty sections. If there are no proposals, print:

```text
Dream complete. Memory quality is clean.
```

## Step 5: Apply

Ask the user to resolve each conflict with `A`, `B`, or `skip`, then ask for
final apply confirmation. Empty conflict answers are `skip`.

Apply in this order:

1. For each merge, call `add_memory` with:
   - `text="<merged content>"`
   - `user_id="<active_user_id>"`
   - `app_id="<active_project_id>"`
   - `metadata={"type": "<type>", "branch": "<active_branch>", "confidence": <max confidence>, "source": "mem0-dream", "merged_from": ["<id1>", "<id2>"]}`
   - `infer=False`
2. Read the returned `event_id`, call `get_event_status(event_id=<event_id>)`,
   and require `status="SUCCEEDED"` with a non-empty `memory_id`. Retrieve that
   replacement with `get_memory(id=<memory_id>)` and verify its merged content
   and project scope. If creation or verification fails, keep both source memories
   and report the failed merge for a later run.
3. Only after replacement verification succeeds, call
   `delete_memory(id=<id1>)` and `delete_memory(id=<id2>)`.
4. For each resolved contradiction, call `delete_memory(id=<loser_id>)`.
5. For each prune, call `delete_memory(id=<memory_id>)`.

Print:

```text
Dream complete - reviewed: <N>, merged: <N>, pruned: <N>, conflicts resolved: <N>, skipped: <N>
```

## See Also

- `/mem0:memory-reviewer` - read-only quality audit.
- `/mem0:forget` - targeted deletion.
- `/mem0:health --deep` - connectivity and quality checks.
