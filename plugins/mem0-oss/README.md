# Mem0 OSS Codex Plugin

This plugin connects Codex to a self-hosted `mem0-oss-mcp` bridge.

The repository copy is environment-driven. It starts the bundled stdio bridge
and expects the Codex process to provide the bridge URL and bearer token:

```env
MEM0_OSS_MCP_URL=http://<bridge-host>:8080/mcp
MEM0_OSS_MCP_TOKEN=change-me
```

For Codex Desktop, generate a local plugin instance instead. Pass the bridge
endpoint and token at install time; the installer writes the token to a local
private dotenv file, while the generated MCP config stores only the URL,
token variable name, and env-file path. This avoids requiring custom
environment variables in the Codex process. The recommended shell flow reads
the token from stdin so it does not appear in process listings:

```bash
printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://<bridge-host>:<bridge-port>/mcp \
  --token-stdin \
  --install
```

The installer writes a local marketplace under `~/.mem0-oss-mcp/codex-plugins`
and rewrites only the generated copy. Token values passed with `--token-stdin`
or `--token` are stored in
`~/.mem0-oss-mcp/codex-plugins/env/<plugin-name>.env` with owner-only
permissions. You can still pass `--env-file /path/to/bridge.env` to choose the
dotenv location yourself.

If you omit `--env-file`, the installer preserves the direct HTTP MCP config
and Codex must receive `MEM0_OSS_MCP_TOKEN` in its process environment before a
new thread starts.

For the full official Mem0 Codex plugin experience, including skills and
lifecycle hooks, initialize the official Mem0 submodule and generate from it:

```bash
git submodule update --init --depth 1 third_party/mem0

printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --url http://<bridge-host>:<bridge-port>/mcp \
  --with-hooks \
  --token-stdin \
  --install
```

`--with-hooks` copies the official plugin from
`third_party/mem0/integrations/mem0-plugin` into the generated local marketplace,
adds a small Mem0 OSS compatibility layer, and merges hook commands into
`~/.codex/hooks.json`. The repository does not vendor official hook files; to
upgrade them, update the submodule commit and rerun the installer.

To use a custom token variable or plugin id:

```bash
printf '%s\n' "$MEM0_HOME_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_codex_plugin.py \
  --name mem0-home \
  --url https://mem0-api.example.com:18443/mcp \
  --token-stdin \
  --token-env-var MEM0_HOME_MCP_TOKEN \
  --with-hooks \
  --install
```

Use `--mcp-transport http` only when you explicitly want Codex to connect to
the HTTP MCP endpoint directly through `bearer_token_env_var`. The default
`--mcp-transport auto` chooses stdio when `--env-file` is present.

## OpenCode

OpenCode's official Mem0 plugin runs native TypeScript tools and hooks. Generate
a local OSS-compatible copy instead of editing the upstream plugin:

```bash
git submodule update --init --depth 1 third_party/mem0

printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | \
  python3 plugins/mem0-oss/scripts/install_opencode_plugin.py \
  --url http://<bridge-host>:<bridge-port>/mcp \
  --token-stdin \
  --install
```

The generated copy lives under `~/.mem0-oss-mcp/opencode-plugins/<name>`. The
installer builds it and writes a small loader into
`~/.config/opencode/plugins/<name>.js`, which OpenCode loads on startup. Token
values passed with `--token-stdin` or `--token` are stored in a local private
dotenv file under `~/.mem0-oss-mcp/opencode-plugins/env/`, not in generated
TypeScript source.
