import { randomUUID } from "node:crypto";
import { Mem0McpError, PiMcpTransport, memorySchema, pageSchema, receiptSchema, listingSchema } from "./mem0_oss_pi_transport.ts";
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
export interface ListOptions {
  readonly filters?: Record<string, string>;
  readonly includeExpired?: boolean;
  readonly pageSize?: number;
  readonly cursor?: string;
  readonly signal?: AbortSignal;
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
  private async readListing(options: ListOptions, mode: "cursor" | "count") {
    const filters = options.filters ?? {};
    requireScope(filters);
    const pageSize = options.pageSize ?? 20;
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      throw new Mem0McpError("Mem0 OSS pageSize must be between 1 and 100");
    }
    const response = await this.transport.call("get_memories", {
      filters, mode, include_expired: options.includeExpired ?? false,
      ...(mode === "cursor" ? { page_size: pageSize, cursor: options.cursor } : {}),
    }, options.signal);
    const parsed = listingSchema.safeParse(response);
    if (!parsed.success) {
      throw new Mem0McpError("Invalid cursor listing response; requires mem0-oss-mcp 0.1.6+ and Sidecar 0.3.13+");
    }
    return parsed.data;
  }
  async getAll(options: ListOptions = {}) {
    const page = await this.readListing(options, "cursor");
    return { results: page.results, count: page.results.length, total: page.total, countBasis: page.count_basis,
      hasMore: page.has_more, nextCursor: page.next_cursor, truncated: page.has_more };
  }
  async countAll(options: Pick<ListOptions, "filters" | "includeExpired" | "signal"> = {}) {
    const page = await this.readListing(options, "count");
    if (page.results.length || page.has_more) throw new Mem0McpError("Invalid Mem0 OSS count-only response");
    return { total: page.total, countBasis: page.count_basis };
  }
  async *iterateAllPages(options: ListOptions = {}) {
    let cursor = options.cursor;
    const seenCursors = new Set<string>();
    let previousIds = new Set<string>();
    if (cursor) seenCursors.add(cursor);
    for (;;) {
      const page = await this.getAll({ ...options, pageSize: options.pageSize ?? 100, cursor });
      const ids = new Set(page.results.map(memory => memory.id));
      if (ids.size !== page.results.length || page.results.some(memory => previousIds.has(memory.id))
        || (page.nextCursor !== null && seenCursors.has(page.nextCursor))) {
        throw new Mem0McpError("Mem0 OSS pagination repeated a cursor or memory ID");
      }
      yield page;
      if (page.nextCursor === null) return;
      seenCursors.add(page.nextCursor);
      cursor = page.nextCursor;
      previousIds = ids;
    }
  }
  private async requireScopedMemory(id: string, filters: Record<string, string>, signal?: AbortSignal): Promise<ReturnType<typeof memorySchema.parse>> {
    requireScope(filters);
    const memory = memorySchema.parse(await this.transport.call("get_memory", { id }, signal));
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
  async delete(id: string, options: { readonly filters: Record<string, string>; readonly signal?: AbortSignal }) {
    await this.requireScopedMemory(id, options.filters, options.signal);
    await this.transport.call("delete_memory", { id }, options.signal);
    return { message: "Memory deleted." };
  }
  async deleteAll(options: EntityOptions & { readonly filters?: Record<string, string>; readonly signal?: AbortSignal } = {}) {
    const filters = entityFilters(options);
    requireScope(filters);
    if (options.filters && Object.keys(options.filters).length) {
      const selected = { ...options.filters, ...filters };
      if (Object.keys(selected).some(key => !["user_id", "agent_id", "run_id", "app_id", "type"].includes(key))) {
        throw new Mem0McpError("Unsupported bulk-delete filter");
      }
      let deleted = 0;
      try {
        for await (const page of this.iterateAllPages({ filters: selected, includeExpired: true, signal: options.signal })) {
          for (const memory of page.results) {
            await this.delete(memory.id, { filters: selected, signal: options.signal });
            deleted += 1;
          }
        }
      } catch (error) {
        if (error instanceof Error) throw new Mem0McpError(`Bulk delete stopped; ${deleted} deletions confirmed.`, { cause: error });
        throw error;
      }
      return { message: `Deleted ${deleted} matching indexed memories from the selected scope.`, deletedCount: deleted };
    } else {
      await this.transport.call("delete_all_memories", filters, options.signal);
    }
    return { message: "Deleted memories in the selected scope." };
  }
}
