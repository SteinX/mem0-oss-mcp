import assert from "node:assert/strict";
import Client from "../plugins/mem0-oss/scripts/oss_adapter/mem0_oss_pi_client.ts";

for (const url of ["http://foo％bar", "http://%EF%BC%8F.test", "http://mem0%7F.test", "http://a٠b.test"]) {
  process.env.MEM0_OSS_BASE_URL = url;
  assert.throws(() => new Client({ apiKey: "list-fixture-key" }), /base URL/);
}

type Mode = "ignored" | "capped" | "normal";
let mode: Mode = "ignored";
const calls: number[] = [];
const mutations: string[] = [];
const rows = [
  { id: "one", metadata: { app_id: "list-project" } },
  { id: "two", metadata: { app_id: "list-project" } },
  { id: "foreign", metadata: { app_id: "other-project" } },
];
const fixture = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch(request) {
  assert.equal(request.headers.get("x-api-key"), "list-fixture-key");
  const url = new URL(request.url);
  if (request.method === "GET") {
    assert.equal(url.pathname, "/memories");
    assert.equal(url.searchParams.get("user_id"), "list-user");
    const limit = Number(url.searchParams.get("top_k"));
    calls.push(limit);
    const results = mode === "capped" && limit !== 1
      ? Array.from({ length: 1000 }, (_, index) => ({ id: `cap-${index}`, metadata: { app_id: "list-project" } }))
      : mode === "ignored" ? rows : rows.slice(0, limit);
    return Response.json({ results });
  }
  assert.equal(mode, "normal", "Incomplete listing allowed a mutation");
  const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
  mutations.push(id);
  const index = rows.findIndex((row) => row.id === id);
  assert(index >= 0 && id !== "foreign");
  rows.splice(index, 1);
  return Response.json({ message: "OK" });
} });
process.env.MEM0_OSS_BASE_URL = `http://127.0.0.1:${fixture.port}`;
process.env.MEM0_OSS_LIST_FETCH_LIMIT = "1500";
process.env.MEM0_OSS_BACKEND_LIST_RETRY_LIMIT = "1000";
const client = new Client({ apiKey: "list-fixture-key" });
const filters = { user_id: "list-user", app_id: "list-project" };
try {
  for (const invalid of ["ignored", "capped"] as const) {
    mode = invalid;
    await assert.rejects(client.deleteAll({ userId: "list-user", appId: "list-project" }), /(?:truncated|list limit)/);
    await assert.rejects(client.update("one", { text: "forbidden", filters }), /(?:truncated|list limit)/);
    await assert.rejects(client.delete("one", { filters }), /(?:truncated|list limit)/);
    assert.equal(mutations.length, 0);
  }
  assert(calls.includes(1), "Backend limit support was not probed");
  mode = "normal";
  process.env.MEM0_OSS_BACKEND_LIST_RETRY_LIMIT = "3";
  const customCap = new Client({ apiKey: "list-fixture-key" });
  await assert.rejects(customCap.deleteAll({ userId: "list-user", appId: "list-project" }), /legacy cap/);
  assert.equal(mutations.length, 0);
  await client.deleteAll({ userId: "list-user", appId: "list-project" });
  assert.deepEqual(mutations, ["one", "two"]);
  assert.deepEqual(rows.map((row) => row.id), ["foreign"]);
  assert.equal((await client.getAll({ filters })).count, 0);
  console.log(JSON.stringify({ verdict: "PASS", ignored_top_k: "zero mutations", silent_legacy_cap: "zero mutations",
    complete_list: "only scoped IDs deleted", requests: calls.length + mutations.length }));
} finally {
  fixture.stop(true);
}
