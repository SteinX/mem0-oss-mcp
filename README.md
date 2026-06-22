# mem0-oss-mcp

Small MCP bridge for the self-hosted Mem0 OSS server from `mem0ai/mem0/server`.

It exposes the Mem0 MCP tool names expected by Codex and forwards them to a
self-hosted Mem0 REST API.

## Configuration

```env
MEM0_OSS_BASE_URL=http://192.168.1.20:18080
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

For a bridge running on the same machine as Codex, add the marketplace and
install the default plugin:

```bash
codex plugin marketplace add SteinX/mem0-oss-mcp
codex plugin add mem0-oss@mem0-oss-mcp
```

The default plugin points at `http://127.0.0.1:8080/mcp` and reads its bearer
token from `MEM0_OSS_MCP_TOKEN`.

For any other host, port, domain, or token environment variable, generate a
local plugin instance instead of editing files in this repository:

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://192.168.2.202:38080/mcp \
  --token-env-var MEM0_OSS_MCP_TOKEN \
  --install
```

The installer writes a local marketplace under
`~/.mem0-oss-mcp/codex-plugins`, patches only that generated copy, and then
installs it through `codex plugin add`. It never writes token values to disk;
configure the named token environment variable in the process that runs Codex.

Multiple instances can use different plugin IDs and token variables:

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --name mem0-home \
  --display-name "Mem0 Home" \
  --url https://mem0-home.example.com:18443/mcp \
  --token-env-var MEM0_HOME_MCP_TOKEN \
  --install
```

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
