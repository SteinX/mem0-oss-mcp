# mem0-oss-mcp

Small MCP bridge for the self-hosted Mem0 OSS server from `mem0ai/mem0/server`.

It exposes the Mem0 MCP tool names expected by Codex and forwards them to a
self-hosted Mem0 REST API.

## Configuration

```env
MEM0_OSS_BASE_URL=http://<mem0-host>:<mem0-port>
MEM0_OSS_API_KEY=m0sk_xxx

MEM0_OSS_MCP_HOST=0.0.0.0
MEM0_OSS_MCP_PORT=8080
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
url = "http://<bridge-host>:8080/mcp"
bearer_token_env_var = "MEM0_OSS_MCP_TOKEN"
```

## Codex plugin

This repository also publishes a Codex plugin marketplace at
`.agents/plugins/marketplace.json`.

To use the checked-in plugin, provide the bridge URL and bearer token in the
Codex process environment, then add the marketplace and install the plugin:

```env
MEM0_OSS_MCP_URL=http://<bridge-host>:8080/mcp
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
  --url http://<bridge-host>:<bridge-port>/mcp \
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
  --url http://<bridge-host>:<bridge-port>/mcp \
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
an OSS routine-maintenance variant that checks listing completeness and records
maintenance run markers.
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
  --url http://<bridge-host>:<bridge-port>/mcp \
  --token-stdin \
  --install
```

The installer writes a generated copy under
`~/.mem0-oss-mcp/opencode-plugins/<name>`, builds it with Bun, and installs a
small loader in `~/.config/opencode/plugins/<name>.js`. OpenCode loads local
plugins from that directory at startup. The generated plugin keeps the upstream
OpenCode hooks, native tools, and skills, while its memory client forwards
operations to `mem0-oss-mcp` through JSON-RPC `tools/call`.

Token values passed with `--token-stdin` or `--token` are written to a local
private dotenv file under `~/.mem0-oss-mcp/opencode-plugins/env/`; they are not
written to generated TypeScript source. Pass `--env-file` when you want to
choose the dotenv path yourself.

To update the upstream OpenCode plugin files, update the `third_party/mem0`
submodule and rerun `install_opencode_plugin.py`.

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
