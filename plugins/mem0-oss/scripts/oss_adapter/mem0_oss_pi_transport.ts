import { z } from "zod";
import { validateMcpUrl } from "./mem0_oss_pi_env.ts";

export class Mem0McpError extends Error {
  constructor(readonly reason: string, options?: ErrorOptions) { super(reason, options); this.name = "Mem0McpError"; }
}
export const memorySchema = z.object({
  id: z.string().min(1), memory: z.string().optional(), event: z.string().optional(),
  score: z.number().nullable().optional(), created_at: z.string().nullable().optional(),
  user_id: z.string().nullable().optional(), agent_id: z.string().nullable().optional(),
  run_id: z.string().nullable().optional(), app_id: z.string().nullable().optional(),
  metadata: z.record(z.string(), z.unknown()).nullable().optional(),
}).passthrough();
export const pageSchema = z.object({
  results: z.array(memorySchema), page: z.number().int().positive().optional(),
  has_more: z.boolean().optional(), total: z.number().int().nonnegative().optional(),
  count: z.number().int().nonnegative().optional(), truncated: z.boolean().optional(),
});
export const receiptSchema = z.object({ event_id: z.string(), status: z.string() });
export const listingSchema = z.object({
  protocol: z.literal("cursor-v1"), results: z.array(memorySchema).max(100),
  total: z.number().int().nonnegative(), count_basis: z.literal("sidecar_projection"),
  next_cursor: z.string().min(1).max(4096).nullable(), has_more: z.boolean(),
  stale_skipped: z.number().int().nonnegative().optional(),
}).refine(page => page.has_more === (page.next_cursor !== null), "Invalid cursor progress");
const envelopeSchema = z.object({
  jsonrpc: z.literal("2.0"), id: z.number(),
  error: z.object({ code: z.number(), message: z.string() }).optional(),
  result: z.object({ isError: z.boolean().optional(), content: z.array(z.object({
    type: z.string(), text: z.string().optional(),
  })) }).optional(),
});

export class PiMcpTransport {
  private readonly url: string;
  private nextId = 0;
  constructor(private readonly token: string, private readonly headers: Record<string, string>) {
    this.url = validateMcpUrl(process.env.MEM0_OSS_MCP_URL || process.env.MEM0_OSS_PI_RESOLVED_MCP_URL || "");
    if (!token.trim()) throw new Mem0McpError("Mem0 OSS MCP token is required");
  }
  async call(name: string, args: Readonly<Record<string, unknown>>, signal?: AbortSignal): Promise<unknown> {
    signal?.throwIfAborted();
    const id = ++this.nextId;
    // A timeout can follow a committed mutation; never replay writes automatically.
    const response = await fetch(this.url, {
      method: "POST", headers: { ...this.headers, "Content-Type": "application/json", Authorization: `Bearer ${this.token}` },
      body: JSON.stringify({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } }),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(60_000)]) : AbortSignal.timeout(60_000), redirect: "error",
    });
    if (!response.ok) throw new Mem0McpError(`Mem0 OSS MCP request failed: HTTP ${response.status}`);
    const parsed = envelopeSchema.safeParse(await response.json());
    if (!parsed.success || parsed.data.id !== id) throw new Mem0McpError("Invalid Mem0 OSS MCP response envelope");
    const { error, result } = parsed.data;
    const text = result?.content.find(part => part.type === "text")?.text;
    if (name === "get_memories" && result?.isError) {
      if (text === "Cursor listing requires Sidecar 0.3.13+"
        || text === "Cursor listing requires a sidecar-backed MCP bridge") {
        throw new Mem0McpError("Cursor listing requires mem0-oss-mcp 0.1.6+ with Sidecar 0.3.13+; upgrade Sidecar, then the bridge");
      }
      const status = /^backend error (\d{3}):/.exec(text ?? "")?.[1];
      if (status === "409") throw new Mem0McpError("Mem0 cursor scan conflicted; restart without a cursor (HTTP 409)");
      if (status === "404" || status === "422") {
        throw new Mem0McpError(`Mem0 cursor endpoint, scope or filters unavailable (HTTP ${status}); check mem0-oss-mcp 0.1.6+ and Sidecar 0.3.13+`);
      }
    }
    if (error || result?.isError) throw new Mem0McpError(`Mem0 OSS MCP tool ${name} failed`);
    if (!text) throw new Mem0McpError("Mem0 OSS MCP response has no JSON content");
    try { return JSON.parse(text); }
    catch (error) {
      if (error instanceof SyntaxError) throw new Mem0McpError("Mem0 OSS MCP response has invalid JSON content");
      throw error;
    }
  }
}
