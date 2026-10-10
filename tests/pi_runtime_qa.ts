import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createAgentSession, SessionManager } from "@earendil-works/pi-coding-agent";
import { fixture, record } from "./pi_mcp_fixture.ts";

const root = process.env.PI_QA_ROOT, plugin = process.env.PI_QA_PLUGIN;
assert(root && plugin, "PI_QA_ROOT and PI_QA_PLUGIN are required");
const user = "pi-mcp-fixture-user", app = "android-reddoc";
const cwd = join(root, "reddoc_dev3"), agentDir = join(root, "agent");
mkdirSync(cwd, { recursive: true }); mkdirSync(agentDir, { recursive: true });
execFileSync("git", ["init", "-q", cwd]);
execFileSync("git", ["-C", cwd, "config", "remote.origin.url", "git@github.com:android/reddoc.git"]);
writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ packages: [plugin] }));
// Given a custom install root, when the host starts without agent-dir env, then its config is used.
writeFileSync(join(agentDir, "mem0-config.json"), JSON.stringify({ userId: user }));
for (const key of ["PI_CODING_AGENT_DIR", "OMO_CODING_AGENT_DIR", "SENPI_CODING_AGENT_DIR", "MEM0_USER_ID", "MEM0_APP_ID"]) delete process.env[key];
const backend = fixture(user, app);
process.env.MEM0_OSS_MCP_URL = backend.url;
process.env.MEM0_OSS_MCP_TOKEN = "fixture-token";
const result = await createAgentSession({ cwd, agentDir, sessionManager: SessionManager.inMemory(cwd) });
const { session } = result;
try {
  assert.equal(result.extensionsResult.errors.length, 0, JSON.stringify(result.extensionsResult.errors));
  const errors: string[] = [];
  await session.bindExtensions({ onError: error => errors.push(error.error) });
  const runner = session.extensionRunner;
  const tool = runner.getToolDefinition("mem0_memory"); assert(tool);
  const ctx = runner.createToolContext("qa", undefined);
  await session.prompt("/mem0-status");
  const status = session.state.messages.find(message => message.role === "custom" && message.customType === "mem0-status");
  assert(status && JSON.stringify(status).includes(app));
  assert.equal(backend.calls[0]?.args["filters"] && JSON.stringify(backend.calls[0].args["filters"]), JSON.stringify({ user_id: user, app_id: app }));
  assert.equal(backend.calls[0]?.args["mode"], "count");
  assert(JSON.stringify(status).includes("Active indexed project memories"));
  // Given changing recall, when two prompts run, then only hidden context changes.
  const first = await runner.emitBeforeAgentStart("first question", undefined, { cwd });
  const second = await runner.emitBeforeAgentStart("second question", undefined, { cwd });
  assert.equal(first.systemPromptOptions.forceSystemPrompt, second.systemPromptOptions.forceSystemPrompt);
  assert(first.messages?.some(message => message.customType === "mem0-recall" && message.display === false));
  assert.notDeepEqual(first.messages, second.messages);
  assert(!first.systemPromptOptions.forceSystemPrompt?.includes("fixture decision"));
  // Given default capture settings, when a turn ends, then no conversation is uploaded.
  const before = backend.calls.length;
  await runner.emit({ type: "agent_end", messages: [{ role: "user", content: "private conversation", timestamp: 0 }] });
  assert.equal(backend.calls.length, before);
  const saved = await tool.execute("qa", { action: "add", content: "explicit decision", metadata: { type: "decision", app_id: "foreign" } }, undefined, undefined, ctx);
  assert(record(saved.details) && typeof saved.details["eventId"] === "string");
  const add = backend.calls.find(call => call.name === "add_memory");
  assert.deepEqual(add?.args["metadata"], { type: "decision", app_id: app });
  assert.equal(add?.args["infer"], false);
  // Given extra filters, when searching, then they narrow results and cannot overwrite identity.
  await tool.execute("qa", { action: "search", query: "fixture", filters: { type: "decision", user_id: "foreign", app_id: "foreign" } }, undefined, undefined, ctx);
  assert.deepEqual(backend.calls.at(-1)?.args["filters"], { type: "decision", user_id: user, app_id: app });
  for (const id of ["foreign-app", "foreign-user"]) {
    await assert.rejects(tool.execute("qa", { action: "delete", memory_id: id }, undefined, undefined, ctx), /scope/);
  }
  await tool.execute("qa", { action: "update", memory_id: "own", content: "changed", metadata: { type: "lesson" } }, undefined, undefined, ctx);
  await tool.execute("qa", { action: "delete", memory_id: "own" }, undefined, undefined, ctx);
  await assert.rejects(tool.execute("qa", { action: "get_all", scope: "global" }, undefined, undefined, ctx), /global/i);
  // Given a large corpus, when touring interactively, then only one bounded UI preview is shown.
  const notifications: string[] = [];
  runner.setUIContext({ ...ctx.ui, notify: message => notifications.push(message) }, "interactive");
  for (let index = 0; index < 120; index += 1) backend.rows.push({ id: `tour-${String(index).padStart(3, "0")}`,
    memory: `huge-tour-memory-${index} ${"x".repeat(10000)}`, user_id: user, metadata: { app_id: app, type: "tour-fixture" } });
  const tourBefore = backend.calls.length;
  await session.prompt("/mem0-tour");
  assert.equal(backend.calls.length, tourBefore + 1);
  assert.equal(backend.calls.at(-1)?.args["page_size"], 50);
  const notice = notifications.at(-1); assert(notice && notice.length <= 4000);
  assert(!session.state.messages.some(message => message.role === "custom" && message.customType === "mem0-tour"));
  // Given paged tool output, when continuing explicitly, then no full corpus enters a tool response.
  const firstPage = await tool.execute("qa", { action: "get_all", filters: { type: "tour-fixture" }, page_size: 20 }, undefined, undefined, ctx);
  assert(record(firstPage.details) && firstPage.details["hasMore"] === true && firstPage.details["returnedCount"] === 20);
  const cursor = firstPage.details["nextCursor"]; assert(typeof cursor === "string");
  assert(JSON.stringify(firstPage.content).length < 12000);
  const nextPage = await tool.execute("qa", { action: "get_all", filters: { type: "tour-fixture" }, page_size: 20, cursor }, undefined, undefined, ctx);
  assert(record(nextPage.details) && nextPage.details["returnedCount"] === 20);
  assert.notDeepEqual(firstPage.content, nextPage.content);
  // Given explicit capture opt-in, when a turn ends, then only redacted typed capture is written.
  writeFileSync(join(agentDir, "mem0-config.json"), JSON.stringify({ userId: user, autoCapture: true }));
  const optedIn = await createAgentSession({ cwd, agentDir, sessionManager: SessionManager.inMemory(cwd) });
  try {
    await optedIn.session.bindExtensions({});
    await optedIn.session.extensionRunner.emit({ type: "agent_end", messages: [
      { role: "user", content: "preference password=fixture-private-secret", timestamp: 0 },
    ] });
    const captured = backend.calls.filter(call => call.name === "add_memory").at(-1);
    assert(captured?.args["infer"] === true);
    assert(record(captured.args["metadata"]) && captured.args["metadata"]["type"] === "auto_capture");
    assert(!JSON.stringify(captured.args["messages"]).includes("fixture-private-secret"));
  } finally { optedIn.session.dispose(); }
  await tool.execute("qa", { action: "delete_all", filters: { type: "decision" } }, undefined, undefined, ctx);
  assert.equal(backend.calls.filter(call => call.name === "delete_all_memories").length, 0);
  assert(backend.rows.some(row => row.metadata["type"] === "auto_capture"));
  await tool.execute("qa", { action: "delete_all" }, undefined, undefined, ctx);
  assert.deepEqual(backend.rows.map(row => row.id), ["foreign-app", "foreign-user"]);
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log(JSON.stringify({ runtime: "Pi 1.1.0", transport: "MCP", hidden_recall: "passed", system_prompt: "stable",
    remote_identity: app, default_capture: "off", scoped_crud: "passed", requests: backend.calls.length }));
} finally { session.dispose(); backend.server.stop(true); }
