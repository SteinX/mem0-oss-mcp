export { initializeMem0OssEnv } from "./mem0_oss_pi_env.ts";
import { validateRestBaseUrl } from "./mem0_oss_pi_env.ts";

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
  readonly source?: string;
  readonly customCategories?: readonly Record<string, string>[];
}

interface Memory {
  readonly id: string;
  readonly memory?: string;
  readonly event?: string;
  readonly score?: number | null;
  readonly created_at?: string | null;
  readonly metadata?: Record<string, unknown> | null;
}

class Mem0RestError extends Error {
  constructor(readonly reason: string) {
    super(reason);
    this.name = "Mem0RestError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMemory(value: unknown): value is Memory {
  return isRecord(value) && typeof value["id"] === "string"
    && (value["memory"] === undefined || typeof value["memory"] === "string")
    && (value["score"] === undefined || value["score"] === null || typeof value["score"] === "number")
    && (value["created_at"] === undefined || value["created_at"] === null || typeof value["created_at"] === "string")
    && (value["event"] === undefined || typeof value["event"] === "string")
    && (value["metadata"] === undefined || value["metadata"] === null || isRecord(value["metadata"]));
}

function memories(response: unknown): Memory[] {
  if (!isRecord(response) || !Array.isArray(response["results"]) || !response["results"].every(isMemory)) {
    throw new Mem0RestError("Mem0 OSS response must contain a results array with memory IDs");
  }
  return response["results"];
}

function entityFilters(options: EntityOptions): Record<string, string> {
  const filters: Record<string, string> = {};
  if (options.userId) filters["user_id"] = options.userId;
  if (options.agentId) filters["agent_id"] = options.agentId;
  if (options.runId) filters["run_id"] = options.runId;
  if (options.appId) filters["app_id"] = options.appId;
  return filters;
}

function requireEntity(filters: Record<string, string>): void {
  if (!filters["user_id"] && !filters["agent_id"] && !filters["run_id"]) {
    throw new Mem0RestError("Mem0 OSS requests require a user, agent or run scope");
  }
}

export default class PiMemoryClient {
  readonly headers: Record<string, string> = {};
  readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly listLimit: number;

  constructor(options: { readonly apiKey: string }) {
    this.apiKey = options.apiKey;
    this.baseUrl = validateRestBaseUrl(process.env.MEM0_OSS_BASE_URL || "");
    if (!this.baseUrl || !this.apiKey) throw new Mem0RestError("Mem0 OSS base URL and API key are required");
    this.listLimit = Number(process.env.MEM0_OSS_LIST_FETCH_LIMIT || "1000");
    if (!Number.isInteger(this.listLimit) || this.listLimit <= 0) {
      throw new Mem0RestError("MEM0_OSS_LIST_FETCH_LIMIT must be a positive integer");
    }
  }

  private async request(method: string, path: string, body?: unknown): Promise<unknown> {
    // Writes are never retried: a timeout can occur after the server committed.
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { ...this.headers, "Content-Type": "application/json", "X-API-Key": this.apiKey },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(60_000),
      redirect: "error",
    });
    if (!response.ok) throw new Mem0RestError(`Mem0 OSS ${method} request failed: HTTP ${response.status}`);
    return response.json();
  }

  async add(messages: readonly { readonly role: string; readonly content: string }[], options: EntityOptions = {}) {
    const filters = entityFilters(options);
    requireEntity(filters);
    const { app_id: appId, ...entities } = filters;
    const result = memories(await this.request("POST", "/memories", {
      messages, ...entities, infer: options.infer ?? true,
      metadata: appId ? { app_id: appId } : {},
    }));
    return { results: result, status: "SUCCEEDED", message: result.length
      ? `Memory operation completed (${result.length} changes).` : "No memory changes were produced." };
  }

  async search(query: string, options: SearchMemoryOptions = {}) {
    const filters = options.filters ?? {};
    requireEntity(filters);
    return { results: memories(await this.request("POST", "/search", {
      query, filters, top_k: options.topK ?? 10, threshold: options.threshold,
    })) };
  }

  private async list(filters: Record<string, string>, showExpired = false) {
    requireEntity(filters);
    const query = new URLSearchParams({ top_k: String(this.listLimit), show_expired: String(showExpired) });
    for (const key of ["user_id", "agent_id", "run_id"]) {
      if (filters[key]) query.set(key, filters[key]);
    }
    const all = memories(await this.request("GET", `/memories?${query}`));
    if (all.length >= this.listLimit) {
      throw new Mem0RestError("Mem0 OSS list may be truncated; increase MEM0_OSS_LIST_FETCH_LIMIT within the server limit. No mutation attempted.");
    }
    const results = filters["app_id"]
      ? all.filter((memory) => memory.metadata?.["app_id"] === filters["app_id"])
      : all;
    return { results, count: results.length };
  }

  async getAll(options: { readonly filters?: Record<string, string> } = {}) {
    return this.list(options.filters ?? {});
  }

  private async requireScopedMemory(id: string, filters: Record<string, string>): Promise<void> {
    const { results } = await this.list(filters, true);
    if (!results.some((memory) => memory.id === id)) {
      throw new Mem0RestError("Memory is absent or outside the selected scope");
    }
  }

  async update(id: string, options: { readonly text: string; readonly filters: Record<string, string> }) {
    await this.requireScopedMemory(id, options.filters);
    await this.request("PUT", `/memories/${encodeURIComponent(id)}`, { text: options.text });
    return { status: "Memory updated." };
  }

  async delete(id: string, options: { readonly filters: Record<string, string> }) {
    await this.requireScopedMemory(id, options.filters);
    await this.request("DELETE", `/memories/${encodeURIComponent(id)}`);
    return { message: "Memory deleted." };
  }

  async deleteAll(options: EntityOptions = {}) {
    const { results } = await this.list(entityFilters(options), true);
    let deleted = 0;
    for (const memory of results) {
      try {
        await this.request("DELETE", `/memories/${encodeURIComponent(memory.id)}`);
        deleted += 1;
      } catch (error) {
        if (error instanceof Error) {
          throw new Mem0RestError(`Deletion stopped after ${deleted}/${results.length} memories: ${error.message}`);
        }
        throw error;
      }
    }
    return { message: `Deleted ${deleted} memories in the selected scope.` };
  }
}
