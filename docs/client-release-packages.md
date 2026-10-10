# Codex and OpenCode release packages

Release attachments contain the full OSS-adapted native runtime and skills.
Codex packages include lifecycle hooks and the MCP stdio bridge; OpenCode packages
include compiled JavaScript with bundled dependencies. No upstream clone or local
build is needed. Python 3.10+ and the installed client are required. The installer
supports Unix-like hosts (Linux, macOS and WSL).

Download `mem0-oss-codex-<version>.zip` or `mem0-oss-opencode-<version>.zip` and its
matching `.sha256` file. Verify the checksum before running downloaded code, then
extract the archive. The installer verifies checksums again when importing it.
The `.tar.gz` alternative uses the same checksum file and supports the same
installer commands; extract it with `tar -xzf` and pass that tarball to `--archive`.

```bash
sha256sum --check mem0-oss-codex-0.1.6.sha256 --ignore-missing
unzip mem0-oss-codex-0.1.6.zip -d ./codex-release
printf '%s\n' "$MEM0_OSS_MCP_TOKEN" | python3 ./codex-release/mem0-oss/install.py install \
  --client codex --archive ./mem0-oss-codex-0.1.6.zip \
  --url https://your-bridge.example/mcp --token-stdin
```

Use `--client opencode` with the OpenCode archive instead. Use `--env-file` to
reference an existing private credential file rather than creating one with
`--token-stdin`. `--token-env-var` selects a custom variable name. Endpoint URLs
must end in `/mcp` without embedded credentials, queries or fragments.

The default root is `~/.mem0-oss`. Each client keeps `connection.json`, optional
owner-only `credentials.env`, and runtime data under `clients/<client>/`, outside
`versions/`. Connection fields are `url`, `env_file`, `token_env_var`, and optional
`user_id` / `app_id`. Environment overrides retain priority. Update and rollback
preserve these files unless new connection options are explicitly supplied.

Codex registers `mem0-oss@mem0-oss-releases` through its native marketplace
commands and enables native hooks. OpenCode installs a small managed loader in
`~/.config/opencode/plugins/mem0-oss.js`. Existing model configuration and unrelated
plugins are preserved. `--root` and `--host-dir` select alternate installation and
client config directories; later operations retain the original host directory.

Before migrating, disable the old Mem0 package and remove its old global memory
hooks or OpenCode loader to avoid duplicate capture. Preserve unrelated hooks and
model authentication extensions. The installer refuses to overwrite an unmanaged
OpenCode `mem0-oss.js`.

Close the client before switching versions. Subsequent operations need no URL or
credential arguments:

```bash
python3 ~/.mem0-oss/clients/codex/current/install.py update --client codex
python3 ~/.mem0-oss/clients/codex/current/install.py rollback --client codex
python3 ~/.mem0-oss/clients/opencode/current/install.py update --client opencode --version 0.1.6
python3 ~/.mem0-oss/clients/opencode/current/install.py rollback --client opencode
```

`update` downloads the latest stable GitHub release by default. `--version` pins a
release; `--archive` works offline. Version directories are retained. A lock
prevents simultaneous installations, a transaction journal recovers interrupted
switches on the next invocation, and registration failures restore the prior
package selection and host configuration. Rollback selects the previous release
while keeping current credentials and runtime data. Restart the host after each
operation.

The unified publisher builds and tests all client archives before uploading them.
Each archive includes `provenance.json`; checksums identify the exact asset bytes.
Manual dispatch can backfill an existing release using a later generator commit.
