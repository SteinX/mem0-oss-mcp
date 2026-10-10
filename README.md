# mem0-oss-mcp

Small MCP bridge for the self-hosted Mem0 OSS server from `mem0ai/mem0/server`.

It exposes the Mem0 MCP tool names expected by Codex and forwards them to a
self-hosted Mem0 REST API.

`add_memory` accepts an optional `idempotency_key` (1–128 visible ASCII
characters) when a Sidecar backend is configured. The bridge forwards it as
the Sidecar `Idempotency-Key` header. Reusing the key with the same payload
returns the recorded result; a different payload conflicts. Direct Core mode
rejects this option because it cannot provide the durable Sidecar contract.

Added memories use the Sidecar's persisted event ID. Event status remains
queryable after a bridge restart. The bridge authenticates the client as usual
and reads only that event's bounded status/results within its configured
project using its private operator credential; this read omits caller
attribution because Sidecar's event endpoint requires an operator principal.
Memory operations continue forwarding the original caller attribution.

## Publishing the container image

Publishing a GitHub Release automatically runs `Publish GHCR image` and builds
its exact tagged commit. `ghcr.io/steinx/mem0-oss-mcp` receives the Release tag
and full commit SHA as image tags. Stable releases also update `latest`;
prereleases do not. Draft releases and Git tag pushes alone do not publish.
Image publications share a queue, and automatic builds update `latest` only
while their tag is still GitHub's latest stable Release after immutable tags
are pushed, immediately before promotion. A delayed older build
preserves the newer alias.

For release tags containing this workflow update, retry publication without
recreating the Release by manually dispatching the workflow from that tag:

```sh
gh workflow run publish-ghcr.yml --repo SteinX/mem0-oss-mcp --ref 0.1.6
```

A manual run from a tag preserves `latest` unless `push_latest=true` is supplied.
Manual runs from `main` retain their existing behavior: publish the commit SHA
and update `latest`. Other branch runs cannot promote `latest`, even when the
input is set. The publisher verifies its source before logging into GHCR.

## Configuration

```env
MEM0_OSS_BASE_URL=http://<mem0-host>:<mem0-port>
MEM0_OSS_API_KEY=m0sk_xxx

# Recommended: route memory operations through the control-plane sidecar.
MEM0_SIDECAR_BASE_URL=http://mem0-platform-sidecar:8765
MEM0_SIDECAR_PROJECT_ID=default
MEM0_SIDECAR_REQUIRED=true
# Required with the sidecar's secure default. This is the private operator
# credential, not a dashboard-created client key.
MEM0_SIDECAR_API_KEY=replace-with-private-operator-key
# Optional; defaults to a per-process ID and a five-minute heartbeat.
# MEM0_SIDECAR_INSTANCE_ID=mem0-oss-mcp-1
# MEM0_SIDECAR_HEARTBEAT_INTERVAL_SECONDS=300

MEM0_OSS_MCP_HOST=0.0.0.0
MEM0_OSS_MCP_PORT=8080
# disabled | static | hybrid | core_api_key
MEM0_OSS_MCP_AUTH_MODE=static
MEM0_OSS_MCP_CLIENT_AUTH_URL=http://mem0:8000/auth/me
MEM0_OSS_MCP_CLIENT_AUTH_TIMEOUT_SECONDS=5
MEM0_OSS_MCP_TOKEN=change-me

MEM0_OSS_DEFAULT_USER_ID=codex
MEM0_OSS_DEFAULT_APP_ID=default
MEM0_OSS_LIST_FETCH_LIMIT=5000
MEM0_OSS_BACKEND_LIST_RETRY_LIMIT=1000
# Optional: only set when backend top_k should differ from MEM0_OSS_LIST_FETCH_LIMIT.
# MEM0_OSS_BACKEND_LIST_FETCH_LIMIT=5000
```

`MEM0_OSS_BASE_URL` is the base URL of your Mem0 OSS REST server. The port is
not assumed.

Clients configure only the public MCP URL and bearer token. `MEM0_SIDECAR_*`
variables are private bridge/operator settings and must not be copied into
Codex, OpenCode, Cursor, Claude, Hermes, or other client configuration.

### MCP client authentication

| Mode | Accepted credentials | Intended use |
| --- | --- | --- |
| `disabled` | none | loopback-only development |
| `static` | `MEM0_OSS_MCP_TOKEN` | backward-compatible deployments |
| `hybrid` | legacy static token or Core API key | migration only |
| `core_api_key` | any active Core admin API key | steady state |

`static` and `hybrid` require a non-empty `MEM0_OSS_MCP_TOKEN`.
When neither a mode nor a legacy token is configured, startup fails closed.
`disabled` must be selected explicitly and requires `MEM0_OSS_MCP_HOST` to be
a loopback address; there is no non-loopback override.
`core_api_key` validates the incoming bearer credential through Core
`/auth/me`; the MCP process receives no static shared secret. Core
unavailability returns a sanitized 503, while an invalid or revoked key
returns 401.

Create one named Core admin API key per client in the dashboard's **Client Keys**
page. Codex and OpenCode continue using `MEM0_OSS_MCP_TOKEN` as their local
environment variable name, but its value becomes that client's `m0sk_...` key.
The endpoint and MCP configuration remain unchanged.

When sidecar routing is enabled with a trusted `MEM0_SIDECAR_API_KEY`, Requests
attributes new rows as `Legacy shared MCP key` for `legacy_static` or by the
Core key label and prefix for `core_api_key`. Without that private bridge
credential, sidecar rejects caller-supplied attribution instead of trusting
it. Historical rows created before attribution remain
`Unknown (pre-attribution)`; they are never guessed or backfilled.

When `MEM0_SIDECAR_BASE_URL` is set, all memory operations plus entity
list/delete use the sidecar so its durable project/app index stays current.
`MEM0_SIDECAR_PROJECT_ID` supplies the project boundary, while each tool call's
concrete `app_id` remains intact. If the sidecar setting is absent, the bridge
keeps the legacy direct-OSS behavior. A sidecar request failure is returned to
the caller and is never retried as a direct write, avoiding accidental
double-writes.

With sidecar routing enabled, the bridge reports a bounded read/write routing
capability heartbeat at startup, during health checks, and every five minutes.
When `MEM0_SIDECAR_REQUIRED=true`, startup fails unless the private operator
credential can call the protected heartbeat route. Server-side
`AUTO_SAFE` consolidation requires a current heartbeat. Set
`MEM0_SIDECAR_REQUIRED=true` in production so a missing sidecar URL fails at
startup instead of silently selecting legacy direct mode.

`get_memories` fetches a larger backend candidate window before applying local
`app_id` and metadata filters. `MEM0_OSS_LIST_FETCH_LIMIT` is the sidecar target
window and, by default, the largest `top_k` sent to the backend. Set
`MEM0_OSS_BACKEND_LIST_FETCH_LIMIT` only when the backend limit must differ. The
default target is 5000. Older Mem0 OSS builds may reject list requests above
1000, so the sidecar retries with
`MEM0_OSS_BACKEND_LIST_RETRY_LIMIT` and returns `degraded_fetch_limit: true`.
When the fetched backend window is full, responses include `truncated: true` and
`complete: false`; consolidation tools should not treat that listing as complete.

Codex should connect to this bridge, not directly to Mem0 OSS:

```toml
[mcp_servers.mem0]
url = "https://<bridge-host>/mcp"
bearer_token_env_var = "MEM0_OSS_MCP_TOKEN"
```

The bridge itself does not terminate TLS. Keep its `0.0.0.0:8080` listener on
a private application network and publish remote MCP only through an HTTPS
reverse proxy or gateway.

## Codex plugin

The full-experience generator supports the current official
`integrations/codex-plugin` shared-runtime layout and the earlier
`integrations/mem0-plugin` layout. It pins its upstream submodule to Mem0
v2.2.1. Native hooks and standalone CLI workers load the same private OSS
credentials; project recall retains the existing user/app boundary. The
generated plugin uses global Codex hooks once, and its manifest omits native
hooks to avoid duplicate lifecycle execution. Search and explicit remember
skills use the OSS bridge's scoped tool parameters.

This repository also publishes a Codex plugin marketplace at
`.agents/plugins/marketplace.json`.

To use the checked-in plugin, provide the bridge URL and bearer token in the
Codex process environment, then add the marketplace and install the plugin:

```env
MEM0_OSS_MCP_URL=https://<bridge-host>/mcp
MEM0_OSS_MCP_TOKEN=change-me
```

```bash
codex plugin marketplace add SteinX/mem0-oss-mcp
codex plugin add mem0-oss@mem0-oss-mcp
```

For Codex Desktop, or for any host, port, domain, or token that should live
outside the Codex process environment, generate a local plugin instance instead
of editing files in this repository. Pass the bridge endpoint and token at
install time; the installer writes the token to a local private dotenv file and
the generated MCP config stores only the endpoint, token variable name, and
dotenv path. The recommended shell flow reads the token from stdin so it does
not appear in process listings.

```bash
printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url https://<bridge-host>/mcp \
  --token-stdin \
  --install
```

The installer writes a local marketplace under
`~/.mem0-oss-mcp/codex-plugins`, patches only that generated copy, and then
installs it through `codex plugin add`. It never writes token values into
`.mcp.json`, hook commands, or repository files. By default, token values passed
with `--token-stdin` or `--token` are stored in
`~/.mem0-oss-mcp/codex-plugins/env/<plugin-name>.env` with owner-only
permissions. You can still pass `--env-file /path/to/bridge.env` to choose the
dotenv location yourself.

For the full official Mem0 Codex plugin experience, including skills and
lifecycle hooks, use the official Mem0 repository submodule as the upstream
plugin source:

```bash
git submodule update --init --depth 1 third_party/mem0

printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url https://<bridge-host>/mcp \
  --token-stdin \
  --token-env-var MEM0_OSS_MCP_TOKEN \
  --with-hooks \
  --install
```

`--with-hooks` copies `third_party/mem0/integrations/mem0-plugin` into the
generated local marketplace, adds a small Mem0 OSS compatibility layer, and
merges the official Codex hook entries into `~/.codex/hooks.json`. Official
hook and skill files stay in the submodule, not vendored into this repository.
The generated full plugin also replaces the upstream `/mem0:dream` skill with
an OSS manual-trigger variant that checks listing completeness and requires
confirmation before applying changes.
To upgrade them:

```bash
git -C third_party/mem0 fetch origin
git -C third_party/mem0 checkout origin/main
git add third_party/mem0
```

Then rerun `install_codex_plugin.py` so the generated local marketplace copy and
hook paths are refreshed.

The default `--mcp-transport auto` chooses stdio when `--env-file` is present.
Use `--mcp-transport http` only when you explicitly want direct HTTP MCP config
with `bearer_token_env_var`; in that mode, Codex must receive the named token
environment variable before a new thread starts.

Multiple instances can use different plugin IDs and token variables:

```bash
printf '%s\n' "$MEM0_HOME_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --name mem0-home \
  --display-name "Mem0 Home" \
  --url https://mem0-home.example.com:18443/mcp \
  --token-stdin \
  --token-env-var MEM0_HOME_MCP_TOKEN \
  --with-hooks \
  --install
```

## OpenCode plugin

OpenCode's official Mem0 plugin is a native TypeScript plugin rather than an
MCP-only config. To use that full plugin experience with Mem0 OSS, generate a
local OpenCode plugin copy from the official Mem0 submodule and overlay the OSS
compatibility client:

```bash
git submodule update --init --depth 1 third_party/mem0

printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_opencode_plugin.py \
  --url https://<bridge-host>/mcp \
  --token-stdin \
  --install
```

The installer writes a generated copy under
`~/.mem0-oss-mcp/opencode-plugins/<name>`, builds it with Bun, and installs a
small loader in `~/.config/opencode/plugins/<name>.js`. OpenCode loads local
plugins from that directory at startup. The generated plugin keeps the upstream
OpenCode hooks, native tools, and skills, while its memory client forwards
operations to `mem0-oss-mcp` through JSON-RPC `tools/call`.

Generated plugins default to explicit capture: periodic message capture is off,
and memories are written through the plugin's explicit memory tools. The
upstream client-side Dream workflow is also off because durable consolidation
belongs on the server. These defaults can be changed deliberately:

- `--auto-capture-mode bounded` samples every tenth message, with normalized
  duplicate suppression and per-session/per-day limits.
- `--auto-capture-mode legacy` restores the upstream every-third-message
  behavior.
- `--client-dream` re-enables the upstream client-side Dream workflow.

Existing `MEM0_OSS_AUTO_CAPTURE_MODE` and `MEM0_DREAM` environment values take
precedence over installer defaults.

Token values passed with `--token-stdin` or `--token` are written to a local
private dotenv file under `~/.mem0-oss-mcp/opencode-plugins/env/`; they are not
written to generated TypeScript source. Pass `--env-file` when you want to
choose the dotenv path yourself.

To update the upstream OpenCode plugin files, update the `third_party/mem0`
submodule and rerun `install_opencode_plugin.py`.

## Pi plugin

Generate the official Pi 0.3.2+ extension with a self-hosted MCP adapter. Pi uses
Bearer authentication against the bridge's `/mcp` endpoint. Use a bridge configured
with Sidecar 0.3.13 or later and bridge 0.1.6 or later for cursor traversal,
write idempotency and caller-bound event receipts. Upgrade Sidecar first, then
the bridge, then regenerate the Pi extension and restart or reload the session.
The Core REST adapter shipped in the initial Pi implementation is replaced;
regenerate it using your MCP URL and MCP token.

```bash
printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_pi_plugin.py \
  --url http://<mcp-host>:<port>/mcp \
  --upstream-plugin-dir /path/to/mem0-checkout \
  --token-stdin \
  --install
```

The default upstream source is `third_party/mem0`; it must include the sibling
`integrations/agent-plugin-core` directory. Missing patch anchors stop generation
before replacing the active installation. Node.js validates URLs with the same
WHATWG semantics as the runtime. Building requires pnpm; `--no-build` uses the
host's TypeScript loader and requires the generated package's `zod` dependency
(`pnpm install --prod` inside the package).

Packages live in `~/.mem0-oss/pi-plugins/<name>`. Tokens stay outside the package
in an owner-only env file. Use `--env-file` and `--token-env-var` for an existing
private file. The older `--api-key*` flag spellings remain aliases for MCP tokens.
Runtime overrides are `MEM0_OSS_MCP_URL`, `MEM0_OSS_MCP_TOKEN` and
`MEM0_OSS_MCP_TOKEN_ENV_VAR`. An endpoint override to a different origin requires
an explicit runtime token; it cannot reuse the installed file token. Credentials,
query strings, fragments and redirects are rejected. Writes are never retried.

`--install` adds the generated package to `settings.json` once and preserves
other settings. Existing packages remain as timestamped backups; build or settings
commit failures retain/restore the active package and credentials. `--pi-dir`
selects both settings and the generated default config directory. Runtime
`OMO_CODING_AGENT_DIR`, `SENPI_CODING_AGENT_DIR`, then `PI_CODING_AGENT_DIR` override
it. For omo native, install with `--pi-dir ~/.omo/agent`, then restart or `/reload`.
Remove the previous Mem0 extension from settings before enabling this copy to
avoid duplicate tools and commands. Preserve any unrelated model auth extension.

Identity is `MEM0_USER_ID` / `userId` for the user, and `MEM0_APP_ID`, then the Git
origin's owner-repository name, then the repository/directory basename for the app.
This keeps separate checkouts of the same remote in the same project scope.

`mem0-config.json` retains `defaultScope`, `contextInjection`, `searchThreshold`
and `autoCapture`. Capture defaults to **false**. Explicit saves use `infer=false`;
opt-in automatic capture is redacted and marked `metadata.type=auto_capture`.
The tool accepts `metadata` on add/update and string `filters` on search/list and
ID operations. Extra filters cannot override the selected user/app/run scope;
filtered bulk deletion supports the bridge's entity and `type` filters only.
Select `/mem0-scope global` explicitly before requesting global tool scope.

The system prompt contains a fixed conservative memory policy. Variable recall
is a hidden `mem0-recall` message. Senpi's `previewSafe` hook returns the same
system prefix during prewarm without fetching memories or consuming recall state;
upstream Pi ignores the optional registration flag. Both runtimes are exercised
in CI. This does not establish a particular provider's cache hit rate or cost.

`get_all` returns one cursor page (20 rows by default, `page_size` up to 100),
with `nextCursor` for explicit continuation. The client `iterateAllPages` method
supports complete traversal beyond 5000 records without hydrating earlier pages
again. `/mem0-status` counts the active Sidecar index without fetching Core rows;
this index count may exceed currently readable rows if stale records remain.
`/mem0-tour` shows a bounded preview in the interactive UI; headless mode returns
only a short summary. Memory bodies from tour do not enter model context.
Older servers fail promptly with upgrade guidance rather than falling back to
numeric pagination. The existing numeric MCP/dashboard listing remains unchanged.

ID mutations first read that ID and verify its scope. Unfiltered bulk deletion
uses the bridge's scoped endpoint; narrowed bulk deletion reads and deletes one
cursor page at a time. Cancellation or failure reports confirmed deletions and
does not retry writes. Add returns the durable event ID. Cloud-only custom
categories and rerank/source body options are ignored.

Run source/build/runtime verification with `tests/run_pi_qa.sh` (Node 24, pnpm,
Bun and the pinned submodule). Production data and daily extension settings are
not touched by these fixtures.
Set `PI_QA_SIDECAR_SOURCE` to a Sidecar 0.3.13+ checkout to also run the real
authenticated HTTP/SQLite chain against 8568 fixture memories. CI pins that
checkout and runs the chain as well as the SDK fixtures.

## Run

```bash
PYTHONPATH=src python3 -m mem0_oss_mcp.server
```

Docker:

```bash
docker build -t mem0-oss-mcp .
docker run --rm -p 8080:8080 --env-file .env mem0-oss-mcp
```

## Tools

- `add_memory`
- `search_memories`
- `get_memories`
- `get_memory`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `delete_entities`
- `list_entities`
- `list_events`
- `get_event_status`

`list_events` and `get_event_status` are implemented locally because the OSS
REST server writes synchronously and does not expose the platform event API.
