import assert from "node:assert/strict";
import PiMemoryClient from "../plugins/mem0-oss/scripts/oss_adapter/mem0_oss_pi_client.ts";
import { fixture } from "./pi_mcp_fixture.ts";

const user = "fixture-user", app = "android-reddoc";
const cancellation = new AbortController();
let cancelOnSecondPage = false, pageRequests = 0;
const backend = fixture(user, app, name => {
  if (name === "get_memories" && cancelOnSecondPage && ++pageRequests === 2) cancellation.abort();
});
process.env.MEM0_OSS_MCP_URL = backend.url;
const client = new PiMemoryClient({ apiKey: "fixture-token" });
function seed(count: number): void {
  backend.rows.push(...Array.from({ length: count }, (_, index) => ({
    id: `scan-${String(index).padStart(5, "0")}`, memory: "fixture", user_id: user,
    metadata: { app_id: app, type: "decision" },
  })));
}
try {
  seed(8568);
  backend.rows.push({ id: "keep", memory: "non-matching", user_id: user, metadata: { app_id: app, type: "lesson" } });
  const deleted = await client.deleteAll({ userId: user, appId: app, filters: { type: "decision" } });
  assert.equal(deleted.deletedCount, 8569);
  assert.deepEqual(backend.rows.map(row => row.id).sort(), ["foreign-app", "foreign-user", "keep"]);
  const pages = backend.calls.flatMap((call, index) => call.name === "get_memories" ? [index] : []);
  assert.equal(pages.length, 86);
  assert.equal(pages[1], 201, "first page must be deleted before the second page is read");
  assert.equal(backend.calls.filter(call => call.name === "delete_all_memories").length, 0);
  seed(250);
  cancelOnSecondPage = true;
  backend.calls.length = 0;
  await assert.rejects(client.deleteAll({ userId: user, appId: app, filters: { type: "decision" }, signal: cancellation.signal }), /100 deletions confirmed/);
  assert.equal(backend.calls.filter(call => call.name === "delete_memory").length, 100);
  assert.equal(backend.rows.filter(row => row.metadata["type"] === "decision").length, 150);
  console.log(JSON.stringify({ filtered_delete: 8569, cursor_pages: 86, foreign_preserved: true, cancellation_confirmed: 100 }));
} finally { backend.server.stop(true); }
