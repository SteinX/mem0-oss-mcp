# Devin CLI with Mem0 OSS

The generator adapts Mem0's portable `integrations/mem0-agent-plugin` and shared
Python core into a local Devin plugin. It needs Python 3.10+ and the pinned Mem0
submodule, with no additional runtime dependencies. Use a current Devin CLI with
Agent Plugins support (introduced in v3000.5.20).

The upstream portable bundle supplies only `search_memories`. Its `remember`
skill assumes automatic extraction, but the bundle has no capture hooks or flush
worker. Its Python runtime also calls Mem0 Platform. Installing that bundle
unchanged therefore does not provide OSS memory writes.

This generator supplies Devin's native manifest, MCP config and lifecycle adapter,
routes the shared Python runtime through the OSS MCP bridge, and replaces the
skills with working OSS instructions. It does not modify the upstream checkout.

## Install

Initialize the pinned upstream source in this repository:

```sh
git submodule update --init --depth 1 third_party/mem0
```

Set `MEM0_OSS_MCP_TOKEN` to a client credential accepted by your MCP bridge. The
URL must be the bridge's `/mcp` endpoint, rather than Core's REST endpoint or the
private Sidecar API. Read the token from stdin so it stays out of command arguments:

```sh
printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_devin_plugin.py \
  --url https://<bridge-host>/mcp \
  --user-id <your-mem0-user-id> \
  --token-stdin \
  --install
```

`--install` invokes `devin plugins install --local --yes <generated-directory>`.
Omit it to generate and inspect the files before running the printed install
command. Devin must be installed and signed in for plugin registration. This
local installation does not register the plugin in your Devin Cloud account.

The default layout is:

```text
~/.mem0-oss-mcp/devin-plugins/
├── mem0-oss/                # Generated native Devin plugin
│   ├── .devin-plugin/plugin.json
│   ├── .mcp.json
│   ├── hooks.json
│   ├── AGENTS.md
│   ├── core/
│   ├── hooks/
│   ├── scripts/
│   └── skills/
├── env/mem0-oss.env         # Selected bearer credential, mode 0600
└── data/mem0-oss/           # Persistent local evidence, pause state, pending work
```

Use `--target-root` to choose another location, `--name` to choose the plugin's
namespace and MCP server name, and `--env-file` / `--token-env-var` for an existing
credential file. The default server is `mem0-oss`; distinct plugin names produce
distinct server names, so they can coexist with an official `mem0` server.
Skills qualify memory tools with their own server name to keep endpoints and
credentials separate when multiple memory plugins are installed.
When an env file is configured, it is authoritative for that plugin; a process
environment token does not override it or replace a missing file credential.
Without either a token input or an env file, the selected variable must be in
Devin's process environment. Only that selected variable is read: missing OSS
credentials never fall back to a hosted `MEM0_API_KEY`, another client's default
token, host plugin options, or the upstream API-key cache. Sidecar operator keys
and settings belong on the bridge, not in this client. Hooks do not cache host
Platform credentials. An explicit `--user-id` takes precedence over inherited
generic plugin user options for both memory operations and diagnostics.

Keep the generated directory and the Python interpreter used to generate it
available. Devin links local plugins to their source directory. Regenerate at the
same path with the same options to update it, then restart Devin. Existing local
evidence and the default private credential file survive regeneration. A changed
upstream runtime contract fails generation before replacing the working plugin.
For a custom env file, repeat `--env-file` when regenerating.

## Behavior

| Surface | OSS behavior |
| --- | --- |
| MCP | All eleven bridge tool names, including explicit add and event status |
| `/<name>:remember` | Actual `add_memory` call with `infer: false`, followed by event verification |
| `/<name>:search` | Explicit search within the resolved user and repository |
| `/<name>:status` | Shared local status plus a scoped, authenticated bridge search |
| `/<name>:pause`, `resume` | Pause/resume native hooks; explicit MCP tools remain available |
| `/<name>:forget` | Explicit, scoped bridge deletion; local evidence can be cleared separately |
| `UserPromptSubmit` | Shared automatic recall on an eligible prompt, once per session with bounded retry |
| `PostToolUse` | Normalize Devin tool names and boolean success/error results into local evidence |
| `Stop` | Record `last_assistant_message` when supplied; do not parse an invented transcript format |
| `PostCompaction`, `SessionEnd` | Extract buffered evidence only when automatic capture is enabled |

Automatic recall is enabled. The shared runtime keeps redacted prompts and tool
evidence locally. Remote automatic extraction is **off by default**; explicit
remember always uses the MCP write tool. To enable background extraction, add
`--auto-capture` when generating. `MEM0_CODE_AUTO_FLUSH=true` or `false` in the
Devin process overrides the generated capture default. Paused or disabled capture
does not start pending work; detached workers check that state before extraction.
Requests already in progress may finish.

Direct Core and Sidecar-backed bridges both work. Add `--durable-writes` only for
a Sidecar-backed bridge: it supplies stable `devin-…` batch keys across retries.
Direct Core rejects those keys, so the default omits them. Direct Core event
status is process-local; query it immediately. Durable event recovery requires
Sidecar. The bridge and Core still determine extraction behavior; Platform-only
category configuration and separate agent/user extraction instructions are not
added by this adapter.

Native hooks use the event's `cwd`, or `DEVIN_PROJECT_DIR` when it is absent. The
shared Git identity determines `app_id`; the default recall scope is `mine` to
retain a concrete user/repository boundary. Set `--user-id` to use the same user
across machines. Explicit tools use the current workspace's `memory_cli.py
status --json` identities as described by the bundled rule and skills. Those
client-side scopes are not an additional server authorization boundary.

The adapter uses `PostCompaction` for compatibility with older supported Devin
releases. It does not also register the newer `PreCompact` / `PostCompact`
events or subagent hooks. Compaction flushes already-recorded evidence.
Capture depends on the event fields actually
provided by Devin; it is not a complete transcript archive. This local package
is generated for its installation path, not a portable release archive.

## Verify

Restart Devin in the target repository and inspect `devin plugins info mem0-oss`,
`/hooks`, and the available MCP tools. Run `/mem0-oss:status`, save one disposable
fact with `/mem0-oss:remember`, then retrieve it with `/mem0-oss:search`. If capture
is enabled, verify a later session can retrieve a fact from the earlier session.

The repository regression suite runs the generated plugin's actual stdio MCP,
native hooks, CLI and detached worker against the real bridge and a temporary
Core REST fixture. It covers explicit save/event lookup, project isolation,
capture, Sidecar retry keys, pause/disable behavior, credential selection and
safe regeneration:

```sh
python3 -m pytest -q tests/test_devin_plugin.py
```

The fixture replaces LLM/vector persistence. It does not establish behavior in
an authenticated Devin installation or with a live extraction provider.

Official contracts: [Devin plugin layout and local installation](https://docs.devin.ai/cli/extensibility/plugins/overview),
[hook input/output and project directory](https://docs.devin.ai/cli/extensibility/hooks/overview),
[lifecycle events](https://docs.devin.ai/cli/extensibility/hooks/lifecycle-hooks),
[MCP configuration](https://docs.devin.ai/cli/extensibility/mcp/configuration).
