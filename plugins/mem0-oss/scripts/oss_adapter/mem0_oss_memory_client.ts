import { existsSync, readFileSync } from "fs";

type JsonObject = Record<string, any>;

const DEFAULT_TOKEN_ENV_VAR = "MEM0_OSS_MCP_TOKEN";

export interface Mem0OssEnvOptions {
  url?: string;
  tokenEnvVar?: string;
  envFile?: string;
}

function parseDotenvValue(raw: string): string {
  const value = raw.trim();
  if (!value) return "";
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1);
  }
  const comment = value.match(/\s+#/);
  return (comment ? value.slice(0, comment.index) : value).trim();
}

function readDotenv(path: string | undefined): Record<string, string> {
  if (!path) return {};
  try {
    if (!existsSync(path)) return {};
    const values: Record<string, string> = {};
    const text = readFileSync(path, "utf8");
    for (const rawLine of text.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) continue;
      const [key, ...rest] = line.split("=");
      values[key.trim()] = parseDotenvValue(rest.join("="));
    }
    return values;
  } catch {
    return {};
  }
}

let cachedDotenvPath: string | undefined;
let cachedDotenv: Record<string, string> | undefined;

function dotenvValues(): Record<string, string> {
  const path = process.env.MEM0_OSS_ENV_FILE;
  if (cachedDotenv && cachedDotenvPath === path) return cachedDotenv;
  cachedDotenvPath = path;
  cachedDotenv = readDotenv(path);
  return cachedDotenv;
}

function resolveMem0OssTokenSync(): string {
  const tokenEnvVar = process.env.MEM0_OSS_MCP_TOKEN_ENV_VAR || DEFAULT_TOKEN_ENV_VAR;
  const dotenv = dotenvValues();
  const candidates = [
    process.env[tokenEnvVar],
    dotenv[tokenEnvVar],
    tokenEnvVar !== DEFAULT_TOKEN_ENV_VAR ? process.env[DEFAULT_TOKEN_ENV_VAR] : undefined,
    tokenEnvVar !== DEFAULT_TOKEN_ENV_VAR ? dotenv[DEFAULT_TOKEN_ENV_VAR] : undefined,
    dotenv.MEM0_API_KEY,
    process.env.MEM0_API_KEY,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export async function resolveMem0OssToken(): Promise<string> {
  return resolveMem0OssTokenSync();
}

export function initializeMem0OssEnv(options: Mem0OssEnvOptions = {}): void {
  if (options.url && !process.env.MEM0_OSS_MCP_URL) {
    process.env.MEM0_OSS_MCP_URL = options.url;
  }
  if (options.tokenEnvVar && !process.env.MEM0_OSS_MCP_TOKEN_ENV_VAR) {
    process.env.MEM0_OSS_MCP_TOKEN_ENV_VAR = options.tokenEnvVar;
  }
  if (options.envFile && !process.env.MEM0_OSS_ENV_FILE) {
    process.env.MEM0_OSS_ENV_FILE = options.envFile;
  }
  if (process.env.MEM0_TELEMETRY === undefined) {
    process.env.MEM0_TELEMETRY = "false";
  }
  const token = resolveMem0OssTokenSync();
  if (token && !process.env.MEM0_API_KEY) {
    process.env.MEM0_API_KEY = token;
  }
}

function ensureApiKey(): string {
  const token = resolveMem0OssTokenSync();
  if (token && !process.env.MEM0_API_KEY) {
    process.env.MEM0_API_KEY = token;
  }
  return token;
}

function mcpUrl(): string {
  const url = (process.env.MEM0_OSS_MCP_URL || "").trim().replace(/\/+$/, "");
  if (!url) throw new Error("MEM0_OSS_MCP_URL is not set");
  return url;
}

function responseText(content: any): string {
  if (!Array.isArray(content) || content.length === 0) return "";
  const first = content[0];
  if (first && typeof first === "object" && "text" in first) return String(first.text ?? "");
  return String(first ?? "");
}

async function callTool(name: string, args: JsonObject = {}): Promise<any> {
  const token = ensureApiKey();
  if (!token) {
    throw new Error("Mem0 OSS MCP token is not set");
  }

  const response = await fetch(mcpUrl(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name, arguments: args },
    }),
  });

  if (!response.ok) {
    throw new Error(`Mem0 OSS MCP request failed: HTTP ${response.status} ${await response.text()}`);
  }

  const envelope: any = await response.json();
  if (envelope.error) {
    throw new Error(envelope.error.message || JSON.stringify(envelope.error));
  }

  const result = envelope.result ?? {};
  if (result.isError) {
    throw new Error(responseText(result.content) || JSON.stringify(result));
  }

  const text = responseText(result.content);
  if (!text) return result;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function normalizeOptions(options: JsonObject = {}): JsonObject {
  const out: JsonObject = { ...options };
  const rename: Record<string, string> = {
    userId: "user_id",
    agentId: "agent_id",
    appId: "app_id",
    runId: "run_id",
    topK: "top_k",
    pageSize: "page_size",
    expirationDate: "expiration_date",
    memoryType: "memory_type",
  };
  for (const [from, to] of Object.entries(rename)) {
    if (out[from] !== undefined && out[to] === undefined) out[to] = out[from];
    delete out[from];
  }
  return out;
}

function eventIdFromPath(path: string): string {
  const clean = path.split("?")[0].replace(/\/+$/, "");
  return decodeURIComponent(clean.slice(clean.lastIndexOf("/") + 1));
}

function queryArgs(path: string): JsonObject {
  const query = path.includes("?") ? path.slice(path.indexOf("?") + 1) : "";
  const values: JsonObject = {};
  for (const [key, value] of new URLSearchParams(query).entries()) {
    values[key] = ["page", "page_size", "top_k"].includes(key) ? Number(value) : value;
  }
  return values;
}

async function dispatchPath(method: string, path: string): Promise<any> {
  const clean = path.split("?")[0].replace(/\/+$/, "");
  const args = queryArgs(path);

  if (clean === "/v1/ping") {
    return { status: "ok" };
  }
  if (clean === "/v1/events" || clean === "/v3/events") {
    return callTool("list_events", args);
  }
  if (clean.startsWith("/v1/event/") || clean.includes("/events/")) {
    return callTool("get_event_status", { ...args, event_id: eventIdFromPath(clean) });
  }
  if (clean === "/v1/entities" || clean === "/v2/entities") {
    return callTool("list_entities", args);
  }
  if (method === "DELETE" && clean.startsWith("/v2/entities/")) {
    const [, , , type, name] = clean.split("/");
    const key = type === "user" ? "user_id" : type === "agent" ? "agent_id" : type === "run" ? "run_id" : undefined;
    if (!key) throw new Error(`unsupported entity delete path: ${path}`);
    return callTool("delete_entities", { [key]: decodeURIComponent(name || "") });
  }

  throw new Error(`unsupported Mem0 Platform endpoint in OSS adapter: ${method} ${path}`);
}

export class MemoryClient {
  apiKey: string;
  host: string;
  client: {
    get: (path: string) => Promise<{ data: any }>;
    delete: (path: string) => Promise<{ data: any }>;
  };

  constructor(options: { apiKey: string; host?: string }) {
    this.apiKey = options.apiKey;
    this.host = options.host || "mem0-oss-mcp";
    this.client = {
      get: async (path: string) => ({ data: await dispatchPath("GET", path) }),
      delete: async (path: string) => ({ data: await dispatchPath("DELETE", path) }),
    };
    void ensureApiKey();
  }

  async ping(): Promise<{ status: string }> {
    return { status: "ok" };
  }

  async add(messages: Array<JsonObject>, options: JsonObject = {}): Promise<any> {
    return callTool("add_memory", { messages, ...normalizeOptions(options) });
  }

  async search(query: string, options: JsonObject = {}): Promise<any> {
    return callTool("search_memories", { query, ...normalizeOptions(options) });
  }

  async getAll(options: JsonObject = {}): Promise<any> {
    return callTool("get_memories", normalizeOptions(options));
  }

  async get(id: string): Promise<any> {
    return callTool("get_memory", { id });
  }

  async update(id: string, options: JsonObject = {}): Promise<any> {
    return callTool("update_memory", { id, ...normalizeOptions(options) });
  }

  async delete(id: string): Promise<any> {
    return callTool("delete_memory", { id });
  }

  async deleteAll(options: JsonObject = {}): Promise<any> {
    return callTool("delete_all_memories", normalizeOptions(options));
  }

  async deleteUsers(options: JsonObject = {}): Promise<any> {
    return callTool("delete_entities", normalizeOptions(options));
  }

  async users(options: JsonObject = {}): Promise<any> {
    return callTool("list_entities", normalizeOptions(options));
  }

  async getProject(_options: JsonObject = {}): Promise<JsonObject> {
    return { customCategories: [], custom_categories: [] };
  }

  async updateProject(_options: JsonObject = {}): Promise<JsonObject> {
    return { message: "Mem0 OSS adapter skipped hosted project configuration." };
  }
}

export default MemoryClient;
