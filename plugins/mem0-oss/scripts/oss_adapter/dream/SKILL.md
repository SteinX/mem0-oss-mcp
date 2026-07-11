---
name: dream
description: Performs routine Mem0 OSS memory maintenance by auditing project memories, merging clear duplicates, pruning expired low-value entries, and recording a run marker. Use for regular memory hygiene, noisy search results, or high memory counts.
---

# Mem0 OSS Dream - Routine Memory Maintenance

This OSS variant is designed for routine use. Treat it as a maintenance pass:
fetch the active project, analyze everything in memory, show the proposed diff,
then apply only after confirmation. Do not perform destructive changes before
the report is shown unless the user explicitly invoked `--auto`.

Run steps in order. Do not skip the fetch completeness checks.

## Modes

- Default / routine mode: read all memories, print the diff report, wait for user
  confirmation, then apply approved changes.
- `--dry-run`: read and report only. Never call `add_memory`, `update_memory`,
  or `delete_memory`.
- `--auto`: non-interactive routine pass. Apply only low-risk merges and prunes;
  skip contradictions and uncertain merges. Use the run marker check below so
  repeated automatic runs do not churn the same project.

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

## Step 2: Check Recent Dream Runs

Search for recent run markers before analysis:

```python
search_memories(
    query="mem0-dream routine run",
    filters={"AND": [
        {"user_id": "<active_user_id>"},
        {"app_id": "<active_project_id>"},
        {"metadata": {"source": "mem0-dream"}},
        {"metadata": {"type": "maintenance_run"}},
    ]},
    top_k=3,
)
```

If `--auto` is set and a marker from the last 24 hours exists, print:

```text
[mem0-dream --auto] project=<id> skipped=recent-run
```

Then stop. In default mode, continue and include the marker age in the report.

## Step 3: Fetch All Project Memories

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

Then store a run marker unless `--dry-run`.

## Step 4: Analyze In Memory

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

## Step 5: Print Report

Use this format:

```text
## dream - OSS routine report

Scope: user=<user_id> app=<app_id>
Reviewed: <N> memories
Completeness: complete | incomplete fetch_limit=<N>
Last run: <date or none>

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

Then store a run marker unless `--dry-run`.

## Step 6: Apply

In default mode, ask the user to resolve each conflict with `A`, `B`, or `skip`,
then ask for final apply confirmation. Empty conflict answers are `skip`.

In `--auto` mode:

- Apply exact duplicate merges.
- Apply near-duplicate merges only when there is no contradiction and the merged
  content is strictly additive.
- Apply prune candidates only when they are policy-expired or very low
  confidence and not unique.
- Skip all contradictions and uncertain merges.

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

## Step 7: Store Run Marker

Unless `--dry-run`, store a concise marker after every completed run:

```python
add_memory(
    text="mem0-dream routine run for <project_id>: reviewed=<N>, merged=<N>, pruned=<N>, conflicts_skipped=<N>.",
    user_id="<active_user_id>",
    app_id="<active_project_id>",
    metadata={
        "type": "maintenance_run",
        "source": "mem0-dream",
        "branch": "<active_branch>",
        "reviewed": <N>,
        "merged": <N>,
        "pruned": <N>,
        "conflicts_skipped": <N>,
    },
    infer=False,
)
```

Print:

```text
Dream complete - reviewed: <N>, merged: <N>, pruned: <N>, conflicts resolved: <N>, skipped: <N>
```

## See Also

- `/mem0:memory-reviewer` - read-only quality audit.
- `/mem0:forget` - targeted deletion.
- `/mem0:health --deep` - connectivity and quality checks.
