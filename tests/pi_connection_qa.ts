import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Client, { initializeMem0OssEnv } from "../plugins/mem0-oss/scripts/oss_adapter/mem0_oss_pi_client.ts";
import { record } from "./pi_mcp_fixture.ts";

const root = mkdtempSync(join(tmpdir(), "pi-mcp-reload-")), envFile = join(root, "key.env");
const calls: { readonly url: string; readonly key: string | null }[] = [];
async function response(request: Request): Promise<Response> {
  const rpc: unknown = await request.json(); assert(record(rpc));
  calls.push({ url: request.url, key: request.headers.get("authorization") });
  return Response.json({ jsonrpc: "2.0", id: rpc["id"], result: { content: [{ type: "text", text: '{"results":[],"has_more":false}' }] } });
}
const old = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: response });
const fresh = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: response });
const vars = ["MEM0_OSS_MCP_URL", "MEM0_OSS_MCP_TOKEN", "MEM0_API_KEY", "MEM0_OSS_PI_RESOLVED_MCP_URL", "MEM0_OSS_PI_RESOLVED_API_KEY", "MEM0_OSS_ENV_FILE"];
for (const name of vars) delete process.env[name];
try {
  // Given reloaded installs, when endpoint and file token change, then resolved outputs never become inputs.
  for (const [url, token] of [
    [`http://127.0.0.1:${old.port}/old/mcp`, "old-token"],
    [`http://127.0.0.1:${old.port}/new/mcp`, "path-token"],
    [`http://127.0.0.1:${fresh.port}/mcp`, "origin-token"],
  ] as const) {
    writeFileSync(envFile, `MEM0_OSS_MCP_TOKEN=${token}\n`, { mode: 0o600 });
    initializeMem0OssEnv({ url, apiKeyEnvVar: "MEM0_OSS_MCP_TOKEN", envFile });
    const client = new Client({ apiKey: process.env.MEM0_OSS_PI_RESOLVED_API_KEY ?? "" });
    await client.getAll({ filters: { user_id: "reload-user" } });
    assert.deepEqual(calls.at(-1), { url, key: `Bearer ${token}` });
    assert.equal(process.env.MEM0_OSS_MCP_URL, undefined);
    assert.equal(process.env.MEM0_API_KEY, undefined);
  }
  // Given a conflicting origin, when only a stored token exists, then credentials are not loaded.
  process.env.MEM0_OSS_MCP_URL = `http://127.0.0.1:${old.port}/mcp`;
  assert.throws(() => initializeMem0OssEnv({ url: `http://127.0.0.1:${fresh.port}/mcp`, apiKeyEnvVar: "MEM0_OSS_MCP_TOKEN", envFile }), /different origin/);
  delete process.env.MEM0_OSS_MCP_URL;
  initializeMem0OssEnv({ url: `http://127.0.0.1:${fresh.port}/mcp`, apiKeyEnvVar: "MEM0_OSS_MCP_TOKEN", envFile: undefined });
  assert.equal(process.env.MEM0_OSS_PI_RESOLVED_API_KEY, undefined);
  // Given hostile URL spellings, when runtime overrides are parsed, then no request is issued.
  for (const url of ["http://foo％bar/mcp", "http://%EF%BC%8F.test/mcp", "http://mem0%7F.test/mcp", "http://a٠b.test/mcp", "http://host\\name/mcp", "http://ho\tst/mcp"]) {
    process.env.MEM0_OSS_MCP_URL = url;
    assert.throws(() => new Client({ apiKey: "fixture" }), /MCP URL/);
  }
  console.log(JSON.stringify({ origin_guard: "passed", reload: "passed", invalid_urls: "passed", requests: calls.length }));
} finally {
  old.stop(true); fresh.stop(true);
  rmSync(root, { recursive: true, force: true });
  for (const name of vars) delete process.env[name];
}
