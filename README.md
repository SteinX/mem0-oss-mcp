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
```

`MEM0_OSS_BASE_URL` is the base URL of your Mem0 OSS REST server. The port is
not assumed.

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

For Codex Desktop, or for any host, port, domain, or token environment variable
that should live outside the Codex process environment, generate a local plugin
instance instead of editing files in this repository. Passing `--env-file` is
recommended for Codex Desktop: the generated MCP config runs a local stdio
bridge that reads the token from the dotenv file and forwards JSON-RPC to the
HTTP MCP endpoint.

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://<bridge-host>:<bridge-port>/mcp \
  --token-env-var MEM0_OSS_MCP_TOKEN \
  --env-file /path/to/bridge.env \
  --install
```

The installer writes a local marketplace under
`~/.mem0-oss-mcp/codex-plugins`, patches only that generated copy, and then
installs it through `codex plugin add`. It never writes token values into the
plugin config; the dotenv file should contain the named token variable, for
example `MEM0_OSS_MCP_TOKEN=change-me`.

For the full official Mem0 Codex plugin experience, including skills and
lifecycle hooks, use the official Mem0 repository submodule as the upstream
plugin source:

```bash
git submodule update --init --depth 1 third_party/mem0

python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://<bridge-host>:<bridge-port>/mcp \
  --token-env-var MEM0_OSS_MCP_TOKEN \
  --with-hooks \
  --env-file /path/to/bridge.env \
  --install
```

`--with-hooks` copies `third_party/mem0/integrations/mem0-plugin` into the
generated local marketplace, adds a small Mem0 OSS compatibility layer, and
merges the official Codex hook entries into `~/.codex/hooks.json`. Official
hook and skill files stay in the submodule, not vendored into this repository.
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
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --name mem0-home \
  --display-name "Mem0 Home" \
  --url https://mem0-home.example.com:18443/mcp \
  --token-env-var MEM0_HOME_MCP_TOKEN \
  --with-hooks \
  --env-file /path/to/bridge.env \
  --install
```

## OpenCode plugin

OpenCode's official Mem0 plugin is a native TypeScript plugin rather than an
MCP-only config. To use that full plugin experience with Mem0 OSS, generate a
local OpenCode plugin copy from the official Mem0 submodule and overlay the OSS
compatibility client:

```bash
git submodule update --init --depth 1 third_party/mem0

python3 plugins/mem0-oss/scripts/install_opencode_plugin.py \
  --url http://<bridge-host>:<bridge-port>/mcp \
  --token-env-var MEM0_OSS_MCP_TOKEN \
  --env-file /path/to/bridge.env \
  --install
```

The installer writes a generated copy under
`~/.mem0-oss-mcp/opencode-plugins/<name>`, builds it with Bun, and installs a
small loader in `~/.config/opencode/plugins/<name>.js`. OpenCode loads local
plugins from that directory at startup. The generated plugin keeps the upstream
OpenCode hooks, native tools, and skills, while its memory client forwards
operations to `mem0-oss-mcp` through JSON-RPC `tools/call`.

Token values are not written to plugin source. Store the named token variable in
the dotenv file, for example:

```dotenv
MEM0_OSS_MCP_TOKEN=change-me
```

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
