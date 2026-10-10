import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fixture } from "./pi_mcp_fixture.ts";

const root = process.env.PI_QA_ROOT, plugin = process.env.PI_QA_PLUGIN;
assert(root && plugin);
for (const runtime of ["@earendil-works/pi-coding-agent", "@code-yeongyu/senpi"]) {
  const agentDir = join(root, `cli-${runtime.replaceAll("/", "-")}`);
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ packages: [plugin] }));
  const backend = fixture("cli-user", "cli-project");
  try {
    const process = Bun.spawn(["node", join(root, "runtime/node_modules", runtime, "dist/bundle/cli.js"),
      "--mode", "json", "--no-session", "--print", "/mem0-status"], {
      env: { ...Bun.env, PI_CODING_AGENT_DIR: agentDir, OMO_CODING_AGENT_DIR: agentDir,
        SENPI_CODING_AGENT_DIR: agentDir, MEM0_USER_ID: "cli-user", MEM0_APP_ID: "cli-project",
        MEM0_OSS_MCP_URL: backend.url, MEM0_OSS_MCP_TOKEN: "fixture-token", MEM0_TELEMETRY: "false" },
      stdout: "pipe", stderr: "pipe",
    });
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(process.stdout).text(), new Response(process.stderr).text(), process.exited,
    ]);
    assert.equal(exitCode, 0, stderr);
    assert(stdout.includes("Connection: connected"), stdout);
    assert.equal(backend.calls[0]?.name, "get_memories");
    assert.equal(backend.calls[0]?.args["mode"], "count");
    assert.deepEqual(backend.calls[0]?.args["filters"], { user_id: "cli-user", app_id: "cli-project" });
    console.log(JSON.stringify({ runtime, cli: "/mem0-status", connection: "connected", requests: backend.calls.length }));
  } finally { backend.server.stop(true); }
}
