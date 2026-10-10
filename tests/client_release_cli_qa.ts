import assert from "node:assert/strict";
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {createHash} from "node:crypto";
import {mkdirSync, readFileSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {z} from "zod";

const [assets, version, root] = process.argv.slice(2);
assert(assets && version && root);
const installer = process.env.CLIENT_RELEASE_QA_INSTALLER || join(import.meta.dir, "../plugins/mem0-oss/scripts/install_client_release.py");
const token = "release-qa-private-token";
const callSchema = z.object({id: z.union([z.string(), z.number()]).optional(), method: z.string(), params: z.record(z.string(), z.unknown()).optional()});
const calls: {readonly name: string; readonly args: Record<string, unknown>}[] = [];
let modelCalls = 0;
const backend = Bun.serve({hostname: "127.0.0.1", port: 0, async fetch(request) {
  if (new URL(request.url).pathname === "/mcp") {
    assert.equal(request.headers.get("authorization"), `Bearer ${token}`);
    const body = callSchema.parse(await request.json());
    let result: unknown = {};
    if (body.method === "initialize") result = {protocolVersion: "2024-11-05", capabilities: {tools: {}}, serverInfo: {name: "release-fixture", version: "1"}};
    if (body.method === "tools/list") result = {tools: [{name: "get_memories", description: "List fixture memories", inputSchema: {type: "object", properties: {user_id: {type: "string"}, app_id: {type: "string"}}}}]};
    if (body.method === "tools/call") {
      const params = z.object({name: z.string(), arguments: z.record(z.string(), z.unknown()).optional()}).parse(body.params);
      calls.push({name: params.name, args: params.arguments ?? {}});
      result = {content: [{type: "text", text: JSON.stringify({results: [{id: "fixture-memory", memory: "fixture fact"}], count: 1})}]};
    }
    if (body.id === undefined) return new Response(null, {status: 204});
    return Response.json({jsonrpc: "2.0", id: body.id, result});
  }
  modelCalls++;
  const body = z.object({stream: z.boolean().optional(), messages: z.array(z.object({role: z.string()}).passthrough()), tools: z.array(z.object({function: z.object({name: z.string()}).passthrough()}).passthrough()).optional()}).passthrough().parse(await request.json());
  const useTool = body.tools?.some((tool) => tool.function.name === "get_memories") && !body.messages.some((message) => message.role === "tool");
  const content = useTool
    ? {tool_calls: [{index: 0, id: "release_tool_call", type: "function", function: {name: "get_memories", arguments: "{}"}}]}
    : {content: "Release QA complete"};
  const finish = useTool ? "tool_calls" : "stop";
  if (!body.stream) return Response.json({id: "qa", object: "chat.completion", created: 1, model: "test", choices: [{index: 0, message: {role: "assistant", ...content}, finish_reason: finish}], usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}});
  const chunk = (delta: Record<string, unknown>, finish_reason: string | null) => JSON.stringify({id: "qa", object: "chat.completion.chunk", created: 1, model: "test", choices: [{index: 0, delta, finish_reason}]});
  return new Response(`data: ${chunk(content, null)}\n\ndata: ${chunk({}, finish)}\n\ndata: [DONE]\n\n`, {headers: {"content-type": "text/event-stream"}});
}});
const cleanEnv: Record<string, string | undefined> = {PATH: process.env.PATH, TMPDIR: "/tmp"};
cleanEnv.HOME = join(root, "home");
cleanEnv.XDG_CONFIG_HOME = join(root, "xdg-config");
cleanEnv.XDG_DATA_HOME = join(root, "xdg-data");
cleanEnv.XDG_CACHE_HOME = join(root, "xdg-cache");
cleanEnv.OPENCODE_DISABLE_MODELS_FETCH = "true";
mkdirSync(cleanEnv.HOME, {recursive: true});
const project = join(root, "project");
mkdirSync(project, {recursive: true});
const run = async (command: readonly string[], env = cleanEnv): Promise<string> => {
  const child = Bun.spawn([...command], {env, cwd: project, stdin: "pipe", stdout: "pipe", stderr: "pipe"});
  child.stdin.write(token);
  child.stdin.end();
  const timer = setTimeout(() => child.kill(), 60_000);
  try {
    const [out, err, status] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
    writeFileSync(join(root, "last-child.stderr"), err);
    assert.equal(status, 0, err + out);
    return out;
  } finally { clearTimeout(timer); }
};
class Rpc {
  readonly child: ChildProcessWithoutNullStreams;
  readonly pending = new Map<number, {resolve: (value: unknown) => void; reject: (error: Error) => void}>();
  private next = 1;
  stderr = "";
  constructor(home: string) {
    this.child = spawn("codex", ["app-server", "--stdio"], {env: {...cleanEnv, CODEX_HOME: home}, cwd: project});
    let buffer = "";
    this.child.stdout.on("data", (data: Buffer) => {
      buffer += data.toString();
      while (buffer.includes("\n")) {
        const end = buffer.indexOf("\n");
        const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        const message = z.object({id: z.number().optional(), result: z.unknown().optional(), error: z.unknown().optional()}).passthrough().parse(JSON.parse(line));
        if (message.id === undefined) continue;
        const pending = this.pending.get(message.id); this.pending.delete(message.id);
        if (message.error) pending?.reject(new Error(JSON.stringify(message.error)));
        else pending?.resolve(message.result);
      }
    });
    this.child.stderr.on("data", (data: Buffer) => { this.stderr += data.toString(); });
  }
  async call(method: string, params: Record<string, unknown>): Promise<unknown> {
    const id = this.next++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {this.pending.delete(id); reject(new Error(`RPC timeout: ${method}`));}, 30_000);
      this.pending.set(id, {resolve: (value) => {clearTimeout(timer); resolve(value);}, reject: (error) => {clearTimeout(timer); reject(error);}});
      this.child.stdin.write(JSON.stringify({id, method, params}) + "\n");
    });
  }
  stop(): void { this.child.kill("SIGTERM"); }
}
const digest = (path: string) => createHash("sha256").update(readFileSync(path)).digest("hex");
try {
  for (const client of ["codex", "opencode"] as const) {
    const callsBeforeClient = calls.length;
    const host = join(root, client);
    mkdirSync(host, {recursive: true});
    if (client === "codex") writeFileSync(join(host, "config.toml"), 'model = "gpt-5.4"\n[features] # native feature flags\ncodex_hooks = true\n');
    else writeFileSync(join(host, "opencode.json"), JSON.stringify({$schema: "https://opencode.ai/config.json", model: "fixture/test", provider: {fixture: {npm: "@ai-sdk/openai-compatible", name: "Fixture", models: {test: {name: "Test", limit: {context: 32768, output: 1024}}}, options: {baseURL: `http://127.0.0.1:${backend.port}/v1`, apiKey: "fixture-model-key"}}}}));
    const installRoot = join(root, "installation");
    const hostConfig = join(host, client === "codex" ? "config.toml" : "opencode.json");
    const hostConfigBefore = readFileSync(hostConfig, "utf8");
    await run(["python3", installer, "install", "--client", client, "--root", installRoot, "--host-dir", host, "--archive", join(assets, `mem0-oss-${client}-${version}.zip`), "--url", `http://127.0.0.1:${backend.port}/mcp`, "--token-stdin"]);
    const config = join(installRoot, "clients", client, "connection.json");
    const secret = join(installRoot, "clients", client, "credentials.env");
    const parsed = z.record(z.string(), z.string()).parse(JSON.parse(readFileSync(config, "utf8")));
    writeFileSync(config, JSON.stringify({...parsed, user_id: "release-user", app_id: "release-project"}));
    const before = [digest(config), digest(secret)];
    const data = join(installRoot, "clients", client, "data");
    mkdirSync(data, {recursive: true});
    writeFileSync(join(data, "preserved-setting.json"), '{"keep":"fixture-setting"}');
    const state = join(installRoot, "clients", client, "installed.json");
    const first = readFileSync(state, "utf8");
    const verify = async () => {
      if (client === "codex") {
        const rpc = new Rpc(host);
        try {
          await rpc.call("initialize", {clientInfo: {name: "release-qa", version: "1"}, capabilities: {experimentalApi: true}});
          const started = z.object({thread: z.object({id: z.string()})}).passthrough().parse(await rpc.call("thread/start", {cwd: project, approvalPolicy: "never", sandbox: "danger-full-access"}));
          const status = z.object({data: z.array(z.object({name: z.string(), tools: z.record(z.string(), z.unknown())}).passthrough())}).passthrough().parse(await rpc.call("mcpServerStatus/list", {threadId: started.thread.id}));
          const server = status.data.find((entry) => Object.hasOwn(entry.tools, "get_memories"));
          assert(server, JSON.stringify(status) + rpc.stderr);
          const result = await rpc.call("mcpServer/tool/call", {threadId: started.thread.id, server: server.name, tool: "get_memories", arguments: {user_id: "release-user", app_id: "release-project"}});
          assert(JSON.stringify(result).includes("fixture fact"));
          const hooks = await rpc.call("hooks/list", {cwd: project});
          assert(JSON.stringify(hooks).includes("adapter.py"), JSON.stringify(hooks));
        } finally { rpc.stop(); }
      } else {
        const env = {...cleanEnv, OPENCODE_CONFIG_DIR: host, OPENCODE_CONFIG: join(host, "opencode.json")};
        const output = await run(["opencode", "run", "--format", "json", "--model", "fixture/test", "Read fixture memory with get_memories"], env);
        writeFileSync(join(root, "opencode-output.jsonl"), output);
        assert(output.includes('"tool":"get_memories"') && output.includes('"status":"completed"') && output.includes("fixture fact"), output + readFileSync(join(root, "last-child.stderr"), "utf8"));
      }
      assert.deepEqual([digest(config), digest(secret)], before);
      assert.equal(readFileSync(join(data, "preserved-setting.json"), "utf8"), '{"keep":"fixture-setting"}');
      const hostConfigNow = readFileSync(hostConfig, "utf8");
      if (client === "codex") assert(hostConfigNow.includes(hostConfigBefore.trim()));
      else assert.equal(hostConfigNow, hostConfigBefore);
    };
    await verify();
    const installedInstaller = join(installRoot, "clients", client, "current/install.py");
    await run(["python3", installedInstaller, "update", "--client", client, "--root", installRoot, "--archive", join(assets, `mem0-oss-${client}-9.9.9.zip`)]);
    await verify();
    await run(["python3", installedInstaller, "rollback", "--client", client, "--root", installRoot]);
    await verify();
    assert.equal(z.object({current: z.string()}).passthrough().parse(JSON.parse(readFileSync(state, "utf8"))).current, z.object({current: z.string()}).passthrough().parse(JSON.parse(first)).current);
    console.log(JSON.stringify({client, install: "passed", upgrade: "passed", rollback: "passed", external_config: "preserved", native_mcp_calls: calls.slice(callsBeforeClient).filter((entry) => entry.name === "get_memories").length}));
  }
  assert(calls.some((entry) => entry.args.user_id === "release-user" && entry.args.app_id === "release-project"));
} finally { backend.stop(true); }
