import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createAgentSession, SessionManager } from "@code-yeongyu/senpi";
import { fixture, record } from "./pi_mcp_fixture.ts";

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
  assert.equal(backend.calls.at(-1)?.args["mode"], "count");
  const notifications: string[] = [];
  const ctx = runner.createToolContext("qa", undefined);
  runner.setUIContext({ ...ctx.ui, notify: message => notifications.push(message) }, "interactive");
  for (let index = 0; index < 120; index += 1) backend.rows.push({ id: `tour-${String(index).padStart(3, "0")}`,
    memory: `huge-senpi-memory ${"x".repeat(10000)}`, user_id: "senpi-fixture-user", metadata: { app_id: "explicit-project" } });
  const tourBefore = backend.calls.length;
  await session.prompt("/mem0-tour");
  assert.equal(backend.calls.length, tourBefore + 1);
  assert.equal(backend.calls.at(-1)?.args["page_size"], 50);
  const notice = notifications.at(-1); assert(notice && notice.length <= 4000);
  assert(!session.state.messages.some(message => message.role === "custom" && message.customType === "mem0-tour"));
  const tool = runner.getToolDefinition("mem0_memory"); assert(tool);
  const listed = await tool.execute("qa", { action: "get_all", page_size: 20 }, undefined, undefined, ctx);
  assert(record(listed.details) && listed.details["hasMore"] === true && listed.details["returnedCount"] === 20);
  console.log(JSON.stringify({ runtime: "Senpi 2026.10.10-9", preview_safe: "passed", preview_requests: 0,
    preview_prefix: "matches real turn", hidden_recall: "passed", explicit_identity: "passed", omo_config: "passed" }));
} finally { session.dispose(); backend.server.stop(true); }

// Senpi keeps host background workers alive after SDK disposal.
process.exit(0);
