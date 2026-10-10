import assert from "node:assert/strict";

export type Row = { readonly id: string; readonly memory: string; readonly user_id: string;
  readonly metadata: Record<string, unknown>; readonly run_id?: string };
export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function fixture(user: string, app: string, onCall?: (name: string, args: Record<string, unknown>) => void) {
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
    onCall?.(name, args);
    const filters = record(args["filters"]) ? args["filters"] : args;
    const selected = ["get_memories", "search_memories", "delete_all_memories"].includes(name) ? rows.filter(row => Object.entries(filters).every(([key, value]) =>
      !["user_id", "app_id", "run_id", "type"].includes(key)
      || (key === "user_id" ? row.user_id : key === "run_id" ? row.run_id : row.metadata[key]) === value)) : [];
    let result: unknown;
    switch (name) {
      case "get_memories": {
        if (args["mode"] === "count") {
          result = { protocol: "cursor-v1", results: [], total: selected.length, count_basis: "sidecar_projection", next_cursor: null, has_more: false };
          break;
        }
        const ordered = [...selected].sort((left, right) => left.id.localeCompare(right.id));
        const incoming: unknown = args["cursor"] ? JSON.parse(String(args["cursor"])) : undefined;
        const after = record(incoming) && typeof incoming["after"] === "string" ? incoming["after"] : "";
        const upper = record(incoming) && typeof incoming["upper"] === "string" ? incoming["upper"] : ordered.at(-1)?.id ?? "";
        const total = record(incoming) ? Number(incoming["total"]) : selected.length;
        const candidates = ordered.filter(row => row.id > after && row.id <= upper);
        const page = candidates.slice(0, Number(args["page_size"] ?? 20));
        const last = page.at(-1);
        const next = last && candidates.length > page.length ? JSON.stringify({ after: last.id, upper, total }) : null;
        result = { protocol: "cursor-v1", results: page, total, count_basis: "sidecar_projection", next_cursor: next, has_more: next !== null };
        break;
      }
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
