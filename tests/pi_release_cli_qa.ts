import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fixture } from "./pi_mcp_fixture.ts";

const root = process.env["PI_RELEASE_QA_ROOT"];
const plugin = process.env["PI_RELEASE_QA_PLUGIN"];
const runtimeRoot = process.env["PI_RELEASE_NODE_MODULES"];
assert(root && plugin && runtimeRoot);
for (const runtime of ["@earendil-works/pi-coding-agent", "@code-yeongyu/senpi"]) {
  const agentDir = join(root, runtime.replaceAll("/", "-"));
  mkdirSync(agentDir, { recursive: true });
  writeFileSync(join(agentDir, "settings.json"), '{"packages":[],"model":"preserved-model"}');
  writeFileSync(join(agentDir, "mem0-config.json"), '{"userId":"archive-user"}');
  const backend = fixture("archive-user", "archive-project");
  const env = { ...Bun.env, PI_CODING_AGENT_DIR: agentDir, OMO_CODING_AGENT_DIR: agentDir,
    SENPI_CODING_AGENT_DIR: agentDir, MEM0_APP_ID: "archive-project", MEM0_USER_ID: "",
    MEM0_OSS_MCP_URL: backend.url, MEM0_OSS_MCP_TOKEN: "fixture-token", MEM0_TELEMETRY: "false" };
  const cli = join(runtimeRoot, runtime, "dist/bundle/cli.js");
  const commands: readonly (readonly string[])[] = [["install", plugin], ["--mode", "json", "--no-session", "--print", "/mem0-status"]];
  try {
    for (const args of commands) {
      const child = Bun.spawn(["node", cli, ...args], { env, stdout: "pipe", stderr: "pipe" });
      const [stdout, stderr, code] = await Promise.all([
        new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited,
      ]);
      assert.equal(code, 0, stderr);
      if (args[0] !== "install") {
        assert(stdout.includes("Connection: connected"), stdout);
        assert(stdout.includes("Auto-capture: off"), stdout);
      }
    }
    const settings: unknown = JSON.parse(readFileSync(join(agentDir, "settings.json"), "utf8"));
    assert(typeof settings === "object" && settings && "model" in settings);
    assert.equal(settings.model, "preserved-model");
    assert.equal(backend.calls.length, 1);
    assert.deepEqual(backend.calls[0]?.args["filters"], { user_id: "archive-user", app_id: "archive-project" });
    assert.equal(backend.calls[0]?.args["mode"], "count");
    console.log(JSON.stringify({ runtime, extracted_package_install: "passed", status: "connected", requests: backend.calls.length }));
  } finally {
    backend.server.stop(true);
  }
}
