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
let legacy = false;
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
      assert.deepEqual(args["filters"], scope);
      if (!args["mode"]) {
        const page = Number(args["page"]), size = Number(args["page_size"]);
        if (page * size > 5000) return new Response("page window must not exceed 5000 records", { status: 422 });
        result = { results: rows.slice((page - 1) * size, page * size), total: rows.length, page, has_more: page * size < rows.length };
      } else if (legacy) {
        result = { results: [], total: 0, has_more: false };
      } else if (args["mode"] === "count") {
        result = { protocol: "cursor-v1", results: [], total: rows.length, count_basis: "sidecar_projection", next_cursor: null, has_more: false };
      } else {
        const offset = repeatPage ? 0 : Number(args["cursor"] ?? 0), size = Number(args["page_size"]);
        const next = offset + size < rows.length ? String(offset + size) : null;
        result = { protocol: "cursor-v1", results: rows.slice(offset, offset + size), total: rows.length,
          count_basis: "sidecar_projection", next_cursor: next, has_more: next !== null };
      }
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
  // Given a real 5000-record numeric window, when listed normally, then return only one bounded page.
  const list = await client.getAll({ filters: scope });
  assert.equal(list.count, 20);
  assert.equal(list.total, 8568);
  assert.equal(list.results.length, 20);
  assert.equal(list.nextCursor, "20");
  assert.equal(calls.filter(call => call.name === "get_memories").length, 1);
  // Given an explicit complete traversal, when iterated, then all rows pass through cursor pages.
  const all = [];
  for await (const page of client.iterateAllPages({ filters: scope })) all.push(...page.results);
  assert.equal(all.length, 8568);
  assert.equal(all.at(-1)?.id, "own-8567");
  assert(calls.filter(call => call.name === "get_memories").every(call => call.args["mode"] === "cursor"));
  // Given count-only status, when queried, then no row data is requested or returned.
  const count = await client.countAll({ filters: scope });
  assert.equal(count.total, 8568);
  assert.equal(calls.at(-1)?.args["mode"], "count");
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
  await assert.rejects(async () => {
    for await (const page of client.iterateAllPages({ filters: scope })) assert(page.results.length > 0);
  }, /pagination/);
  repeatPage = false;
  // Given a legacy bridge ignoring cursor options, when read, then fail after one request with upgrade guidance.
  legacy = true;
  calls.length = 0;
  await assert.rejects(client.getAll({ filters: scope }), /0\.1\.6.*0\.3\.13/);
  assert.equal(calls.length, 1);
  legacy = false;
  const cancelled = new AbortController(); cancelled.abort();
  calls.length = 0;
  await assert.rejects(client.getAll({ filters: scope, signal: cancelled.signal }), /abort|cancel/i);
  assert.equal(calls.length, 0);
  console.log(JSON.stringify({ transport: "MCP", rows: all.length, bounded_page: list.results.length, scoped_ids: "passed", receipts: "passed", failures: "passed" }));
} finally { server.stop(true); }
