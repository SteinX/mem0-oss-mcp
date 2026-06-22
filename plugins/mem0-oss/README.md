# Mem0 OSS Codex Plugin

This plugin connects Codex to a self-hosted `mem0-oss-mcp` bridge.

The repository copy defaults to a local bridge:

```toml
[mcp_servers.mem0]
url = "http://127.0.0.1:8080/mcp"
bearer_token_env_var = "MEM0_OSS_MCP_TOKEN"
```

For another host or port, generate a local plugin instance. When you pass
`--env-file`, the generated MCP config uses a local stdio bridge that reads the
token from that dotenv file and forwards requests to the HTTP MCP endpoint.
This is the recommended mode for Codex Desktop because it does not require
custom environment variables in the Codex process:

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://192.168.2.202:38080/mcp \
  --env-file /path/to/bridge.env \
  --install
```

The installer writes a local marketplace under `~/.mem0-oss-mcp/codex-plugins`
and rewrites only the generated copy. It stores the env-file path and token
variable name in `.mcp.json`, not the token value. The env file should contain
the token variable, for example:

```dotenv
MEM0_OSS_MCP_TOKEN=change-me
```

If you omit `--env-file`, the installer preserves the direct HTTP MCP config
and Codex must receive `MEM0_OSS_MCP_TOKEN` in its process environment before a
new thread starts.

For the full official Mem0 Codex plugin experience, including skills and
lifecycle hooks, initialize the official Mem0 submodule and generate from it:

```bash
git submodule update --init --depth 1 third_party/mem0

python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://192.168.2.202:38080/mcp \
  --with-hooks \
  --env-file /path/to/bridge.env \
  --install
```

`--with-hooks` copies the official plugin from
`third_party/mem0/integrations/mem0-plugin` into the generated local marketplace,
adds a small Mem0 OSS compatibility layer, and merges hook commands into
`~/.codex/hooks.json`. The repository does not vendor official hook files; to
upgrade them, update the submodule commit and rerun the installer.

To use a custom token variable or plugin id:

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --name mem0-home \
  --url https://mem0-api.example.com:18443/mcp \
  --token-env-var MEM0_HOME_MCP_TOKEN \
  --with-hooks \
  --env-file /path/to/bridge.env \
  --install
```

Use `--mcp-transport http` only when you explicitly want Codex to connect to
the HTTP MCP endpoint directly through `bearer_token_env_var`. The default
`--mcp-transport auto` chooses stdio when `--env-file` is present.
