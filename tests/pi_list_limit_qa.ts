import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Client, { initializeMem0OssEnv } from "../plugins/mem0-oss/scripts/oss_adapter/mem0_oss_pi_client.ts";

for (const url of ["http://foo％bar", "http://%EF%BC%8F.test", "http://mem0%7F.test", "http://a٠b.test"]) {
  process.env.MEM0_OSS_BASE_URL = url;
  assert.throws(() => new Client({ apiKey: "list-fixture-key" }), /base URL/);
}

const reloadRoot = mkdtempSync(join(tmpdir(), "pi-reload-"));
const envFile = join(reloadRoot, "key.env");
const reloadCalls: { readonly url: string; readonly key: string | null }[] = [];
function reloadResponse(request: Request): Response {
  reloadCalls.push({ url: request.url, key: request.headers.get("x-api-key") });
  return Response.json({ results: [] });
}
const oldEndpoint = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: reloadResponse });
const newEndpoint = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: reloadResponse });
const reloadVars = ["MEM0_OSS_BASE_URL", "MEM0_OSS_API_KEY", "MEM0_API_KEY", "MEM0_OSS_PI_RESOLVED_BASE_URL", "MEM0_OSS_PI_RESOLVED_API_KEY"];
for (const name of reloadVars) delete process.env[name];
process.env.MEM0_OSS_ENV_FILE = envFile;
try {
  for (const [url, key] of [
    [`http://127.0.0.1:${oldEndpoint.port}/old`, "old-fixture-key"],
    [`http://127.0.0.1:${oldEndpoint.port}/new`, "path-fixture-key"],
    [`http://127.0.0.1:${newEndpoint.port}/fresh`, "origin-fixture-key"],
  ] as const) {
    writeFileSync(envFile, `MEM0_OSS_API_KEY=${key}\n`, { mode: 0o600 });
    initializeMem0OssEnv({ url, apiKeyEnvVar: "MEM0_OSS_API_KEY", envFile });
    const apiKey = process.env.MEM0_OSS_PI_RESOLVED_API_KEY || process.env.MEM0_API_KEY || "";
    await new Client({ apiKey }).getAll({ filters: { user_id: "reload-fixture-user" } });
    const call = reloadCalls.at(-1);
    assert(call);
    const requestUrl = new URL(call.url);
    assert.equal(requestUrl.origin + requestUrl.pathname, `${url}/memories`);
    assert.equal(requestUrl.searchParams.get("user_id"), "reload-fixture-user");
    assert.equal(call.key, key);
  }
  assert.equal(process.env.MEM0_OSS_BASE_URL, undefined);
  assert.equal(process.env.MEM0_API_KEY, undefined);
  delete process.env.MEM0_OSS_ENV_FILE;
  initializeMem0OssEnv({ url: `http://127.0.0.1:${newEndpoint.port}/fresh`, apiKeyEnvVar: "MEM0_OSS_API_KEY", envFile: undefined });
  assert.equal(process.env.MEM0_OSS_PI_RESOLVED_API_KEY, undefined, "Reload retained a previous file-backed key");
  process.env.MEM0_API_KEY = "explicit-legacy-fixture-key";
  initializeMem0OssEnv({ url: `http://127.0.0.1:${newEndpoint.port}/fresh`, apiKeyEnvVar: "MEM0_OSS_API_KEY", envFile: undefined });
  assert.equal(process.env.MEM0_OSS_PI_RESOLVED_API_KEY, "explicit-legacy-fixture-key");
  assert.equal(process.env.MEM0_API_KEY, "explicit-legacy-fixture-key");
} finally {
  oldEndpoint.stop(true);
  newEndpoint.stop(true);
  rmSync(reloadRoot, { recursive: true, force: true });
  for (const name of [...reloadVars, "MEM0_OSS_ENV_FILE"]) delete process.env[name];
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
    complete_list: "only scoped IDs deleted", same_process_reload: "path/origin/key refreshed", reload_requests: reloadCalls.length,
    requests: calls.length + mutations.length }));
} finally {
  fixture.stop(true);
}
