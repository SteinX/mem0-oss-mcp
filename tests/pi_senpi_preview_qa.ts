import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createAgentSession, SessionManager } from "@code-yeongyu/senpi";
import { fixture } from "./pi_mcp_fixture.ts";

const root = process.env.PI_QA_ROOT, plugin = process.env.PI_QA_PLUGIN;
assert(root && plugin);
const cwd = join(root, "senpi-checkout"), agentDir = join(root, "omo-agent");
mkdirSync(cwd, { recursive: true }); mkdirSync(agentDir, { recursive: true });
writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ packages: [plugin] }));
writeFileSync(join(agentDir, "mem0-config.json"), JSON.stringify({ userId: "senpi-fixture-user" }));
process.env.OMO_CODING_AGENT_DIR = agentDir;
process.env.PI_CODING_AGENT_DIR = join(root, "wrong-pi-dir");
process.env.MEM0_APP_ID = "explicit-project";
const backend = fixture("senpi-fixture-user", "explicit-project");
process.env.MEM0_OSS_MCP_URL = backend.url;
process.env.MEM0_OSS_MCP_TOKEN = "fixture-token";
const result = await createAgentSession({ cwd, agentDir, sessionManager: SessionManager.inMemory(cwd) });
const { session } = result;
try {
  assert.equal(result.extensionsResult.errors.length, 0, JSON.stringify(result.extensionsResult.errors));
  await session.bindExtensions({});
  const runner = session.extensionRunner;
  // Given a preview-safe hook, when prewarming, then no recall or one-shot state is consumed.
  assert.deepEqual(runner.getPreviewUnsafeBeforeAgentStartPaths(), []);
  const preview = await runner.emitBeforeAgentStart("", undefined, "fixed prefix", { cwd }, { preview: true });
  assert(preview);
  assert.equal(backend.calls.length, 0);
  assert.equal(preview.messages?.length ?? 0, 0);
  const turn = await runner.emitBeforeAgentStart("first real prompt", undefined, "fixed prefix", { cwd });
  assert(turn);
  assert.equal(turn.systemPrompt, preview.systemPrompt);
  assert(turn.messages?.some(message => message.customType === "mem0-recall" && message.display === false));
  assert.deepEqual(backend.calls[0]?.args["filters"], { user_id: "senpi-fixture-user", app_id: "explicit-project" });
  // Given the alternate runtime, when a native command runs, then the bridge is reachable.
  await session.prompt("/mem0-status");
  console.log(JSON.stringify({ runtime: "Senpi 2026.10.10-9", preview_safe: "passed", preview_requests: 0,
    preview_prefix: "matches real turn", hidden_recall: "passed", explicit_identity: "passed", omo_config: "passed" }));
} finally { session.dispose(); backend.server.stop(true); }

// Senpi keeps host background workers alive after SDK disposal.
process.exit(0);
