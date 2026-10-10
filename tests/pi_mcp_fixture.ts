import assert from "node:assert/strict";

export type Row = { readonly id: string; readonly memory: string; readonly user_id: string;
  readonly metadata: Record<string, unknown>; readonly run_id?: string };
export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function fixture(user: string, app: string) {
  const rows: Row[] = [
    { id: "own", memory: "fixture decision", user_id: user, metadata: { app_id: app, type: "decision" } },
    { id: "foreign-app", memory: "other project", user_id: user, metadata: { app_id: "another-app" } },
    { id: "foreign-user", memory: "other user", user_id: "another-user", metadata: { app_id: app } },
  ];
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const server = Bun.serve({ hostname: "127.0.0.1", port: 0, async fetch(request) {
    assert.equal(request.headers.get("authorization"), "Bearer fixture-token");
    assert.equal(new URL(request.url).pathname, "/mcp");
    const rpc: unknown = await request.json();
    assert(record(rpc) && record(rpc["params"]));
    const { name, arguments: args } = rpc["params"];
    assert(typeof name === "string" && record(args));
    calls.push({ name, args });
    const filters = record(args["filters"]) ? args["filters"] : args;
    const selected = rows.filter(row => Object.entries(filters).every(([key, value]) =>
      !["user_id", "app_id", "run_id", "type"].includes(key)
      || (key === "user_id" ? row.user_id : key === "run_id" ? row.run_id : row.metadata[key]) === value));
    let result: unknown;
    switch (name) {
      case "get_memories": result = { results: selected, total: selected.length, page: args["page"], has_more: false }; break;
      case "search_memories": result = { results: selected.map(row => ({ ...row, memory: `${row.memory}: ${args["query"]}` })) }; break;
      case "get_memory": result = rows.find(row => row.id === args["id"]); break;
      case "add_memory": {
        assert(Array.isArray(args["messages"]) && record(args["messages"][0]));
        const message = args["messages"][0];
        assert(typeof message["content"] === "string" && typeof args["user_id"] === "string" && record(args["metadata"]));
        rows.push({ id: `added-${rows.length}`, memory: message["content"], user_id: args["user_id"], metadata: args["metadata"],
          ...(typeof args["run_id"] === "string" ? { run_id: args["run_id"] } : {}) });
        result = { event_id: `event-${rows.length}`, status: "SUCCEEDED" };
        break;
      }
      case "update_memory": case "delete_memory": {
        const index = rows.findIndex(row => row.id === args["id"]);
        const row = rows[index]; assert(row);
        if (name === "delete_memory") rows.splice(index, 1);
        else {
          assert(typeof args["text"] === "string");
          rows[index] = { ...row, memory: args["text"], metadata: record(args["metadata"]) ? args["metadata"] : row.metadata };
        }
        result = { message: "OK" }; break;
      }
      case "delete_all_memories":
        for (const row of selected) rows.splice(rows.indexOf(row), 1);
        result = { deleted: selected.length }; break;
      default: assert.fail(`unexpected MCP tool ${name}`);
    }
    return Response.json({ jsonrpc: "2.0", id: rpc["id"], result: { content: [{ type: "text", text: JSON.stringify(result) }] } });
  } });
  return { server, calls, rows, url: `http://127.0.0.1:${server.port}/mcp` };
}
