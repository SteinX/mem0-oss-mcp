import { randomUUID } from "node:crypto";
import { Mem0McpError, PiMcpTransport, memorySchema, pageSchema, receiptSchema } from "./mem0_oss_pi_transport.ts";
export { initializeMem0OssEnv } from "./mem0_oss_pi_env.ts";

export interface SearchMemoryOptions {
  readonly filters?: Record<string, string>;
  readonly threshold?: number;
  readonly topK?: number;
  readonly rerank?: boolean;
  readonly source?: string;
}
interface EntityOptions {
  readonly userId?: string;
  readonly agentId?: string;
  readonly runId?: string;
  readonly appId?: string;
  readonly infer?: boolean;
  readonly metadata?: Record<string, unknown>;
  readonly source?: string;
  readonly customCategories?: readonly Record<string, string>[];
}
function entityFilters(options: EntityOptions): Record<string, string> {
  const filters: Record<string, string> = {};
  if (options.userId) filters["user_id"] = options.userId;
  if (options.agentId) filters["agent_id"] = options.agentId;
  if (options.runId) filters["run_id"] = options.runId;
  if (options.appId) filters["app_id"] = options.appId;
  return filters;
}
function requireScope(filters: Record<string, string>): void {
  const entities = [filters["user_id"], filters["agent_id"], filters["run_id"]];
  if (!entities.some(value => value?.trim() && !/^\*+$/.test(value.trim()))
    || Object.values(filters).some(value => !value.trim() || /^\*+$/.test(value.trim()))) {
    throw new Mem0McpError("Mem0 OSS requests require an explicit entity scope");
  }
}

export default class PiMemoryClient {
  readonly headers: Record<string, string> = {};
  readonly apiKey: string;
  private readonly transport: PiMcpTransport;
  constructor(options: { readonly apiKey: string }) {
    this.apiKey = options.apiKey;
    this.transport = new PiMcpTransport(this.apiKey, this.headers);
  }
  async add(messages: readonly { readonly role: string; readonly content: string }[], options: EntityOptions = {}) {
    const filters = entityFilters(options);
    requireScope(filters);
    const receipt = receiptSchema.parse(await this.transport.call("add_memory", {
      messages, ...filters, infer: options.infer ?? false,
      metadata: { type: options.infer ? "auto_capture" : "memory", ...options.metadata,
        ...(filters["app_id"] ? { app_id: filters["app_id"] } : {}) },
      idempotency_key: randomUUID(),
    }));
    return { eventId: receipt.event_id, status: receipt.status, message: "Memory stored.", results: [] };
  }
  async search(query: string, options: SearchMemoryOptions = {}) {
    const filters = options.filters ?? {};
    requireScope(filters);
    const page = pageSchema.parse(await this.transport.call("search_memories", {
      query, filters, top_k: options.topK ?? 10, threshold: options.threshold,
    }));
    return { results: page.results };
  }
  async getAll(options: { readonly filters?: Record<string, string>; readonly includeExpired?: boolean } = {}) {
    const filters = options.filters ?? {};
    requireScope(filters);
    const results: ReturnType<typeof memorySchema.parse>[] = [];
    const seen = new Set<string>();
    for (let page = 1; ; page += 1) {
      const response = pageSchema.parse(await this.transport.call("get_memories", {
        filters, page, page_size: 100, include_expired: options.includeExpired ?? false,
      }));
      if ((response.page !== undefined && response.page !== page)
        || response.results.some(memory => seen.has(memory.id))) {
        throw new Mem0McpError("Mem0 OSS pagination made no progress or repeated a page");
      }
      for (const memory of response.results) {
        if (seen.has(memory.id)) throw new Mem0McpError("Mem0 OSS pagination repeated a memory ID");
        seen.add(memory.id); results.push(memory);
      }
      if (response.has_more && ((response.total !== undefined && page * 100 >= response.total)
        || (response.total === undefined && response.results.length === 0))) {
        throw new Mem0McpError("Mem0 OSS pagination has inconsistent progress");
      }
      if (response.has_more === false) break;
      if (response.has_more === undefined) {
        if (response.truncated || (response.count ?? results.length) > results.length) {
          throw new Mem0McpError("Mem0 OSS pagination requires a sidecar-backed MCP bridge");
        }
        break;
      }
    }
    return { results, count: results.length };
  }
  private async requireScopedMemory(id: string, filters: Record<string, string>): Promise<ReturnType<typeof memorySchema.parse>> {
    requireScope(filters);
    const memory = memorySchema.parse(await this.transport.call("get_memory", { id }));
    if (memory.id !== id || Object.entries(filters).some(([key, value]) =>
      (memory[key] ?? memory.metadata?.[key]) !== value)) {
      throw new Mem0McpError("Memory is absent or outside the selected scope");
    }
    return memory;
  }
  async update(id: string, options: { readonly text: string; readonly filters: Record<string, string>; readonly metadata?: Record<string, unknown> }) {
    const memory = await this.requireScopedMemory(id, options.filters);
    if (options.metadata && Object.entries(options.filters).some(([key, value]) =>
      options.metadata?.[key] !== undefined && options.metadata[key] !== value)) {
      throw new Mem0McpError("Metadata cannot change the selected scope");
    }
    await this.transport.call("update_memory", { id, text: options.text, metadata: options.metadata ? { ...memory.metadata, ...options.metadata } : undefined });
    return { status: "Memory updated." };
  }
  async delete(id: string, options: { readonly filters: Record<string, string> }) {
    await this.requireScopedMemory(id, options.filters);
    await this.transport.call("delete_memory", { id });
    return { message: "Memory deleted." };
  }
  async deleteAll(options: EntityOptions & { readonly filters?: Record<string, string> } = {}) {
    const filters = entityFilters(options);
    requireScope(filters);
    if (options.filters && Object.keys(options.filters).length) {
      const selected = { ...options.filters, ...filters };
      if (Object.keys(selected).some(key => !["user_id", "agent_id", "run_id", "app_id", "type"].includes(key))) {
        throw new Mem0McpError("Unsupported bulk-delete filter");
      }
      const { results } = await this.getAll({ filters: selected, includeExpired: true });
      for (const memory of results) await this.delete(memory.id, { filters: selected });
    } else {
      await this.transport.call("delete_all_memories", filters);
    }
    return { message: "Deleted memories in the selected scope." };
  }
}
