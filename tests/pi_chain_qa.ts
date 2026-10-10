import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createAgentSession, SessionManager } from "@earendil-works/pi-coding-agent";
import { record } from "./pi_mcp_fixture.ts";

const root = process.env.PI_QA_ROOT, plugin = process.env.PI_QA_PLUGIN, core = process.env.PI_QA_CORE_URL;
assert(root && plugin && core);
const { default: PiMemoryClient }: typeof import("../plugins/mem0-oss/scripts/oss_adapter/mem0_oss_pi_client.ts") = await import(join(plugin, "mem0_oss_pi_client.ts"));
const client = new PiMemoryClient({ apiKey: "mcp-fixture-token" });
const filters = { user_id: "qa-user", app_id: "qa-app" };
async function reads(): Promise<number> {
  const result: unknown = await (await fetch(`${core}/qa/stats`, { headers: { "X-API-Key": "core-fixture-token" } })).json();
  assert(record(result) && typeof result["reads"] === "number");
  return result["reads"];
}
const before = await reads();
assert.equal((await client.countAll({ filters })).total, 8568);
assert.equal(await reads(), before);
let seen = 0, pages = 0, maxPageReads = 0;
let previous = await reads();
for await (const page of client.iterateAllPages({ filters })) {
  const current = await reads();
  maxPageReads = Math.max(maxPageReads, current - previous);
  assert(current - previous <= 100);
  assert.equal(page.total, 8568);
  seen += page.results.length; pages += 1; previous = current;
}
assert.equal(seen, 8568);
assert.equal(pages, 86);
assert.equal(maxPageReads, 100);
const typed = await client.countAll({ filters: { ...filters, type: "decision" } });
assert.equal(typed.total, 3565, "exact type after record 5000 must be counted");
const agentDir = join(root, "chain-agent"); mkdirSync(agentDir, { recursive: true });
writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ packages: [plugin] }));
process.env.MEM0_USER_ID = filters.user_id; process.env.MEM0_APP_ID = filters.app_id;
process.env.MEM0_OSS_MCP_TOKEN = "mcp-fixture-token";
for (const key of ["PI_CODING_AGENT_DIR", "OMO_CODING_AGENT_DIR", "SENPI_CODING_AGENT_DIR"]) delete process.env[key];
const result = await createAgentSession({ cwd: root, agentDir, sessionManager: SessionManager.inMemory(root) });
try {
  assert.equal(result.extensionsResult.errors.length, 0);
  await result.session.bindExtensions({});
  await result.session.prompt("/mem0-status");
  assert(JSON.stringify(result.session.state.messages).includes("8568"));
  const runner = result.session.extensionRunner;
  const ctx = runner.createToolContext("chain", undefined), notices: string[] = [];
  runner.setUIContext({ ...ctx.ui, notify: text => notices.push(text) }, "interactive");
  await result.session.prompt("/mem0-tour");
  assert(notices.at(-1)?.includes("8568"));
  assert(!result.session.state.messages.some(message => message.role === "custom" && message.customType === "mem0-tour"));
  const tool = runner.getToolDefinition("mem0_memory"); assert(tool);
  const page = await tool.execute("chain", { action: "get_all", page_size: 20 }, undefined, undefined, ctx);
  assert(record(page.details) && page.details["returnedCount"] === 20 && page.details["hasMore"] === true);
  const deleted = await client.deleteAll({ userId: filters.user_id, appId: filters.app_id, filters: { type: "remove" } });
  assert.equal(deleted.deletedCount, 3);
  assert.equal((await client.countAll({ filters })).total, 8565);
  console.log(JSON.stringify({ chain: "Pi SDK -> authenticated MCP HTTP -> authenticated Sidecar HTTP -> SQLite/Core HTTP",
    seen, pages, maxPageReads, countCoreReads: 0, typedAfter5000: typed.total, filteredDelete: deleted.deletedCount }));
} finally { result.session.dispose(); }
