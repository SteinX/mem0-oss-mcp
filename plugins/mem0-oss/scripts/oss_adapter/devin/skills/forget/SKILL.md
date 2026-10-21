---
name: forget
description: Delete explicitly selected memories within the current user and repository.
---

Run `{{CLI}} status --json` in the current workspace to obtain concrete
`user_id` and `app_id`. Use `get_memories` with those top-level arguments to
inspect the selected memories, following pagination and completeness markers.
Do not treat a truncated listing as the whole collection.

For an explicit request to clear this user's memories in this repository, call
`delete_all_memories` with both concrete identities. For selected IDs, read each
with `get_memory`, verify its user/app scope, then call `delete_memory`. Explain
the deletion scope and seek clarification only if the user's request does not
identify it. Never broaden deletion to shared or other users' memories without
an explicit request. Report the actual backend result.

For a request to clear local evidence only, use `{{CLI}} forget --yes`.
This clears local evidence for the current repository and does not delete remote
memories. Do not use the upstream `forget --remote` path: its Platform listing
contract is not part of this adapter.
