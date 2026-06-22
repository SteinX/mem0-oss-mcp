# Mem0 OSS Codex Plugin

This plugin connects Codex to a self-hosted `mem0-oss-mcp` bridge.

The repository copy defaults to a local bridge:

```toml
[mcp_servers.mem0]
url = "http://127.0.0.1:8080/mcp"
bearer_token_env_var = "MEM0_OSS_MCP_TOKEN"
```

For another host or port, generate a local plugin instance:

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://192.168.2.202:38080/mcp \
  --install
```

The installer writes a local marketplace under `~/.mem0-oss-mcp/codex-plugins`
and rewrites only the generated copy. It stores the token variable name, not
the token value. Set `MEM0_OSS_MCP_TOKEN` in the environment where Codex runs.

To use a custom token variable or plugin id:

```bash
python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --name mem0-home \
  --url https://mem0-api.example.com:18443/mcp \
  --token-env-var MEM0_HOME_MCP_TOKEN \
  --install
```
