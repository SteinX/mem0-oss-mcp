import assert from "node:assert/strict";
import PiMemoryClient from "../plugins/mem0-oss/scripts/oss_adapter/mem0_oss_pi_client.ts";

const scope = { user_id: "fixture-user", app_id: "android-reddoc" };
const rows = Array.from({ length: 8568 }, (_, index) => ({
  id: `own-${index}`, memory: `fact ${index}`, user_id: scope.user_id,
  metadata: { app_id: scope.app_id },
}));
const calls: { name: string; args: Record<string, unknown> }[] = [];
let repeatPage = false;
let reject = false;
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const server = Bun.serve({ hostname: "127.0.0.1", port: 0, async fetch(request) {
  assert.equal(request.headers.get("authorization"), "Bearer fixture-token");
  assert.equal(request.headers.get("x-api-key"), null);
  if (reject) return new Response("private backend detail", { status: 401 });
  assert.equal(new URL(request.url).pathname, "/mcp");
  const rpc: unknown = await request.json();
  assert(record(rpc) && record(rpc["params"]));
  const params = rpc["params"];
  assert(typeof params["name"] === "string" && record(params["arguments"]));
  const name = params["name"], args = params["arguments"];
  calls.push({ name, args });
  let result: unknown;
  switch (name) {
    case "get_memories": {
      const page = repeatPage ? 1 : Number(args["page"]);
      assert.equal(args["page_size"], 100);
      assert.deepEqual(args["filters"], scope);
      result = { results: rows.slice((page - 1) * 100, page * 100), total: rows.length,
        page, page_size: 100, has_more: page * 100 < rows.length };
      break;
    }
    case "get_memory":
      result = args["id"] === "foreign" ? { id: "foreign", user_id: "another-user", metadata: { app_id: scope.app_id } }
        : rows.find(row => row.id === args["id"]);
      break;
    case "search_memories": result = { results: [rows[0]] }; break;
    case "add_memory": result = { event_id: "event-1", status: "SUCCEEDED" }; break;
    case "update_memory": case "delete_memory": result = { message: "OK" }; break;
    case "delete_all_memories": result = { deleted: rows.length }; break;
    default: assert.fail(`unexpected tool ${name}`);
  }
  return Response.json({ jsonrpc: "2.0", id: rpc["id"], result: { content: [{ type: "text", text: JSON.stringify(result) }] } });
} });
process.env.MEM0_OSS_MCP_URL = `http://127.0.0.1:${server.port}/mcp`;
process.env.MEM0_OSS_BASE_URL = `http://127.0.0.1:${server.port}`;
const client = new PiMemoryClient({ apiKey: "fixture-token" });
try {
  // Given a scope larger than Core's maximum list window, when listed, then all pages are visible.
  const list = await client.getAll({ filters: scope });
  assert.equal(list.count, 8568);
  assert.equal(list.results.at(-1)?.id, "own-8567");
  assert.equal(calls.filter(call => call.name === "get_memories").length, 86);
  // Given an existing ID, when changed, then validation uses a scoped ID read without listing.
  calls.length = 0;
  await client.update("own-8567", { text: "updated", filters: scope });
  assert.deepEqual(calls.map(call => call.name), ["get_memory", "update_memory"]);
  // Given a foreign user in the same app, when deleting, then no mutation is submitted.
  await assert.rejects(client.delete("foreign", { filters: scope }), /scope/);
  assert.equal(calls.filter(call => call.name === "delete_memory").length, 0);
  // Given an explicit typed memory, when saved, then its durable receipt and metadata survive.
  const receipt = await client.add([{ role: "user", content: "explicit fact" }],
    { userId: scope.user_id, appId: scope.app_id, infer: false, metadata: { type: "decision" } });
  assert.equal(receipt.eventId, "event-1");
  assert.equal(receipt.status, "SUCCEEDED");
  const add = calls.find(call => call.name === "add_memory");
  assert.deepEqual(add?.args["metadata"], { type: "decision", app_id: scope.app_id });
  assert.equal(add?.args["infer"], false);
  // Given an HTTP error containing private details, when searching, then only a safe error is exposed.
  reject = true;
  await assert.rejects(client.search("fixture", { filters: scope }), /HTTP 401$/);
  reject = false;
  // Given a server repeating pages, when listing, then partial data is never reported as complete.
  repeatPage = true;
  await assert.rejects(client.getAll({ filters: scope }), /pagination/);
  console.log(JSON.stringify({ transport: "MCP", rows: list.count, pages: 86, scoped_ids: "passed", receipts: "passed", failures: "passed" }));
} finally { server.stop(true); }
