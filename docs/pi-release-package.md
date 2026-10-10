# Mem0 OSS Pi package

Requires MCP 0.1.6+ and Sidecar 0.3.13+. This package includes compiled JavaScript,
bundled runtime dependencies and native skills. Node.js and an installed Pi or
Senpi host are required; you do not need Python, pnpm or an upstream checkout.

Download the zip or tar.gz and the `.sha256` file from the same GitHub release.
Verify the archive against its matching checksum line, then extract it into a
persistent directory. Pi loads local packages from that directory without copying.

```bash
mkdir -p ~/.mem0-oss/pi-release
unzip mem0-oss-pi-0.1.6.zip -d ~/.mem0-oss/pi-release
pi install ~/.mem0-oss/pi-release/mem0-oss
```

Set `MEM0_OSS_MCP_URL` to your bridge URL ending in `/mcp` in the environment used
to launch Pi. Supply your token through `MEM0_OSS_MCP_TOKEN`, or use
`MEM0_OSS_ENV_FILE` pointing to an existing private credential file outside the
package. `MEM0_OSS_MCP_TOKEN_ENV_VAR` selects a different variable name. No endpoint,
token, private env file or build-machine config directory is embedded in the archive.

The host's `getAgentDir()` determines the default `mem0-config.json` directory;
`OMO_CODING_AGENT_DIR`, `SENPI_CODING_AGENT_DIR` and `PI_CODING_AGENT_DIR` can override
it, in that order. Keep your existing `userId` or set `MEM0_USER_ID` to keep the
same memory scope. `MEM0_APP_ID` overrides automatic Git remote project detection.
Auto-capture defaults to off; existing explicit capture configuration is respected.

For Omo/Senpi, register the extracted directory with that host's package install
command or its settings file. Remove the previous memory extension/package entry
before loading this copy to avoid duplicate commands; preserve unrelated model-auth
extensions. Restart or `/reload`, then check `/mem0-status`.

For updates, close the host or unload this package, preserve the old extracted
directory, and extract the new archive at the same path. Local packages are updated
by replacing these files, rather than `pi update` downloading a new archive.
