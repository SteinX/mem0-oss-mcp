import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { createAgentSession, SessionManager } from "@earendil-works/pi-coding-agent";

const root = process.env.PI_QA_ROOT;
const plugin = process.env.PI_QA_PLUGIN;
assert(root && plugin, "PI_QA_ROOT and PI_QA_PLUGIN are required");
const live = process.argv.includes("--live");
const user = `pi-rest-qa-${Date.now()}`;
const cwd = join(root, user);
const app = basename(cwd);
const agentDir = join(root, `agent-${user}`);
mkdirSync(cwd, { recursive: true });
mkdirSync(agentDir, { recursive: true });
writeFileSync(join(agentDir, "settings.json"), JSON.stringify({ packages: [plugin] }));
writeFileSync(join(agentDir, "mem0-config.json"), JSON.stringify({ autoCapture: !live }));
process.env.PI_CODING_AGENT_DIR = agentDir;
process.env.MEM0_USER_ID = user;
process.env.MEM0_TELEMETRY = "false";

type WireCall = { readonly method: string; readonly path: string; readonly body: Record<string, unknown>; readonly query: URLSearchParams };
type Row = { readonly id: string; readonly memory: string; readonly metadata: Record<string, string> };
const calls: WireCall[] = [];
const rows: Row[] = [{ id: "foreign", memory: "other project", metadata: { app_id: "other-project" } }];
let rejectRequests = false;
let invalidSearch = false;
let saturatedList = false;
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const fixture = live ? undefined : Bun.serve({
  hostname: "127.0.0.1", port: 0,
  async fetch(request) {
    if (rejectRequests) return new Response("Unauthorized", { status: 401 });
    assert.equal(request.headers.get("x-api-key"), "fixture-'secret");
    assert.equal(request.headers.get("authorization"), null);
    const url = new URL(request.url);
    const body: unknown = request.method === "POST" || request.method === "PUT" ? await request.json() : {};
    assert(isRecord(body));
    calls.push({ method: request.method, path: url.pathname, body, query: url.searchParams });
    if (url.pathname === "/search") {
      return Response.json({ results: invalidSearch ? [{ memory: "invalid" }] : rows.filter((row) => {
        assert(isRecord(body["filters"]));
        return !body["filters"]["app_id"] || row.metadata["app_id"] === body["filters"]["app_id"];
      }) });
    }
    if (url.pathname === "/memories" && request.method === "GET") {
      assert.equal(url.searchParams.get("user_id"), user);
      return Response.json({ results: saturatedList ? Array.from({ length: 1000 }, () => rows[0]) : rows });
    }
    if (url.pathname === "/memories" && request.method === "POST") {
      assert.equal(body["user_id"], user);
      assert(isRecord(body["metadata"]) && typeof body["metadata"]["app_id"] === "string");
      assert(Array.isArray(body["messages"]));
      const message: unknown = body["messages"][0];
      assert(isRecord(message) && typeof message["content"] === "string");
      const row = { id: `fixture-${rows.length}`, memory: message["content"], metadata: { app_id: body["metadata"]["app_id"] } };
      rows.push(row);
      return Response.json({ results: [{ ...row, event: "ADD" }] });
    }
    const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    const index = rows.findIndex((row) => row.id === id);
    assert(index >= 0);
    if (request.method === "DELETE") rows.splice(index, 1);
    else if (request.method === "PUT") {
      const old = rows[index];
      assert(old && typeof body["text"] === "string");
      rows[index] = { ...old, memory: body["text"] };
    } else assert.fail(`Unexpected REST request ${request.method} ${url.pathname}`);
    return Response.json({ message: "OK" });
  },
});
if (fixture) {
  process.env.MEM0_OSS_BASE_URL = `http://127.0.0.1:${fixture.port}`;
  delete process.env.MEM0_OSS_API_KEY;
  delete process.env.MEM0_API_KEY;
  const envFile = join(agentDir, "key.env");
  writeFileSync(envFile, "MEM0_OSS_API_KEY='fixture-'\"'\"'secret'\n", { mode: 0o600 });
  process.env.MEM0_OSS_ENV_FILE = envFile;
}

const { session } = await createAgentSession({ cwd, agentDir, sessionManager: SessionManager.inMemory(cwd) });
const errors: string[] = [];
await session.bindExtensions({ onError: (error) => errors.push(error.error) });
const runner = session.extensionRunner;
const tool = runner.getToolDefinition("mem0_memory");
assert(tool, `mem0_memory missing: ${errors.join(", ")}`);
const context = runner.createToolContext("qa", undefined);
const commands = runner.getRegisteredCommands().map((command) => command.name);
assert.deepEqual(commands.filter((name) => name.startsWith("mem0-")).sort(), [
  "mem0-forget", "mem0-remember", "mem0-scope", "mem0-search", "mem0-status", "mem0-tour",
]);
function textOf(result: { readonly content: readonly { readonly type: string; readonly text?: string }[] }): string {
  return result.content.filter((part) => part.type === "text").map((part) => part.text).join("\n");
}

try {
  await session.prompt("/mem0-status");
  const status = session.state.messages.find((message) => message.role === "custom" && message.customType === "mem0-status");
  assert(status && JSON.stringify(status).includes("Connection: connected"));
  if (!live) {
    const added = await tool.execute("qa", { action: "add", content: "fixture explicit memory" }, undefined, undefined, context);
    assert(isRecord(added.details) && added.details["status"] === "SUCCEEDED" && added.details["eventId"] === null);
    const output = await runner.emitBeforeAgentStart("earlier fixture work", undefined, { cwd });
    assert(output.systemPromptOptions.forceSystemPrompt?.includes("<mem0-relevant-memories>"));
    assert.deepEqual(calls.find((call) => call.path === "/search")?.body["filters"], { user_id: user, app_id: app });
    await runner.emit({ type: "agent_end", messages: [
      { role: "user", content: "fixture preference password=fixture-secret", timestamp: Date.now() },
    ] });
    const captured = calls.filter((call) => call.method === "POST" && call.path === "/memories").at(-1);
    assert(captured && !JSON.stringify(captured.body["messages"]).includes("fixture-secret"));
    assert.deepEqual(captured.body["metadata"], { app_id: app });
    await assert.rejects(tool.execute("qa", { action: "search", query: "fixture", scope: "global" }, undefined, undefined, context), /global/i);
    await session.prompt("/mem0-scope session");
    await tool.execute("qa", { action: "search", query: "fixture" }, undefined, undefined, context);
    const scoped = calls.at(-1)?.body["filters"];
    assert(isRecord(scoped) && typeof scoped["run_id"] === "string");
    await session.prompt("/mem0-scope project");
    const own = rows.find((row) => row.metadata["app_id"] === app);
    assert(own);
    await tool.execute("qa", { action: "update", memory_id: own.id, content: "updated fact" }, undefined, undefined, context);
    const listed = await tool.execute("qa", { action: "get_all" }, undefined, undefined, context);
    assert(textOf(listed).includes("updated fact") && !textOf(listed).includes("other project"));
    saturatedList = true;
    const before = calls.filter((call) => call.method === "DELETE").length;
    await assert.rejects(tool.execute("qa", { action: "delete_all" }, undefined, undefined, context), /truncated/);
    assert.equal(calls.filter((call) => call.method === "DELETE").length, before);
    saturatedList = false;
    await tool.execute("qa", { action: "delete_all" }, undefined, undefined, context);
    assert.deepEqual(rows.map((row) => row.id), ["foreign"]);
    assert(calls.some((call) => call.query.get("show_expired") === "true"));
    invalidSearch = true;
    await assert.rejects(tool.execute("qa", { action: "search", query: "fixture" }, undefined, undefined, context), /results array/);
    invalidSearch = false;
    rejectRequests = true;
    await assert.rejects(tool.execute("qa", { action: "search", query: "fixture" }, undefined, undefined, context), /HTTP 401/);
    rejectRequests = false;
    console.log(JSON.stringify({ mode: "fixture", transport: "REST", pi: "1.1.0", requests: calls.length,
      recall: "passed", capture: "passed", scopes: "passed", crud: "passed", failures: "passed", project_delete: "isolated" }));
  } else {
    const marker = `Pi direct OSS canary ${user}`;
    try {
      await session.prompt(`/mem0-remember ${marker}`);
      const listed = await tool.execute("qa", { action: "get_all" }, undefined, undefined, context);
      assert(textOf(listed).includes(marker), "Canary not visible through native Pi listing");
      const id = textOf(listed).match(/\[mem0:([0-9a-f-]{36})\]/i)?.[1];
      assert(id, "Canary memory ID missing");
      const found = await tool.execute("qa", { action: "search", query: marker }, undefined, undefined, context);
      assert(textOf(found).includes(marker));
      await tool.execute("qa", { action: "update", memory_id: id, content: `${marker} updated` }, undefined, undefined, context);
      assert(textOf(await tool.execute("qa", { action: "get_all" }, undefined, undefined, context)).includes("updated"));
      console.log(JSON.stringify({ mode: "live", transport: "direct Core REST", pi: "1.1.0", user, app_id: app, memory_id: id,
        write: "passed", list: "passed", search: "passed", update: "passed" }));
    } finally {
      await tool.execute("qa", { action: "delete_all" }, undefined, undefined, context);
      const empty = await tool.execute("qa", { action: "get_all" }, undefined, undefined, context);
      assert(isRecord(empty.details) && empty.details["totalCount"] === 0);
      console.log(JSON.stringify({ cleanup: "canary scope empty after native delete_all" }));
    }
  }
  assert.equal(errors.length, 0, errors.join(", "));
} finally {
  await runner.emit({ type: "session_shutdown", reason: "quit" });
  session.dispose();
  fixture?.stop(true);
}
assert(!readFileSync(join(agentDir, "settings.json"), "utf8").includes("fixture-"));
