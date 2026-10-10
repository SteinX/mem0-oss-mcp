import {readFileSync} from "node:fs";
import {homedir} from "node:os";
import {dirname, join, resolve} from "node:path";
import {
  initializeMem0OssEnv as initializeEnvironment,
  type Mem0OssEnvOptions,
} from "./mem0_oss_memory_client";

type ReleaseConnection = {
  readonly url: string;
  readonly envFile: string | undefined;
  readonly tokenEnvVar: string;
  readonly userId: string | undefined;
  readonly appId: string | undefined;
};

class OpenCodeConnectionConfigError extends Error {
  readonly name = "OpenCodeConnectionConfigError";
  constructor(readonly reason: string) {
    super(`Mem0 OSS OpenCode connection config: ${reason}`);
  }
}

const CONNECTION_FIELDS = ["url", "env_file", "token_env_var", "user_id", "app_id"] as const;
const DEFAULT_TOKEN_ENV_VAR = "MEM0_OSS_MCP_TOKEN";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringField(config: Record<string, unknown>, name: string): string | undefined {
  const value = config[name];
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !value.trim() || /[\x00-\x1f\x7f]/.test(value)) {
    throw new OpenCodeConnectionConfigError(`invalid ${name} field`);
  }
  return value.trim();
}

function validateUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch (error) {
    if (error instanceof TypeError) throw new OpenCodeConnectionConfigError("invalid MCP URL");
    throw error;
  }
  if (!/^https?:\/\//i.test(value) || /[\\\x00-\x1f\x7f]/.test(value)
    || !["http:", "https:"].includes(url.protocol) || !url.hostname
    || url.username || url.password || url.search || url.hash
    || !url.pathname.replace(/\/+$/, "").endsWith("/mcp")) {
    throw new OpenCodeConnectionConfigError("MCP URL must use http(s), without credentials, query or fragment, ending in /mcp");
  }
  return url.href.replace(/\/+$/, "");
}

function expandHome(path: string): string {
  return path.startsWith("~/") ? join(homedir(), path.slice(2)) : path;
}

function loadConnection(): ReleaseConnection {
  const path = expandHome(process.env.MEM0_OSS_CLIENT_CONFIG
    || join(homedir(), ".mem0-oss", "clients", "opencode", "connection.json"));
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    if (error instanceof Error) throw new OpenCodeConnectionConfigError("cannot read a valid JSON file");
    throw error;
  }
  if (!isRecord(raw) || Object.keys(raw).some((key) => !CONNECTION_FIELDS.some((field) => field === key))) {
    throw new OpenCodeConnectionConfigError("expected an object with supported connection fields");
  }
  const url = stringField(raw, "url");
  if (!url) throw new OpenCodeConnectionConfigError("url is required");
  const tokenEnvVar = stringField(raw, "token_env_var") || DEFAULT_TOKEN_ENV_VAR;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenEnvVar)) {
    throw new OpenCodeConnectionConfigError("invalid token_env_var field");
  }
  const envFile = stringField(raw, "env_file");
  return {
    url: validateUrl(url),
    envFile: envFile ? resolve(dirname(path), expandHome(envFile)) : undefined,
    tokenEnvVar,
    userId: stringField(raw, "user_id"),
    appId: stringField(raw, "app_id"),
  };
}

export function initializeMem0OssEnv(
  options: Pick<Mem0OssEnvOptions, "autoCaptureMode" | "clientDream"> = {},
): void {
  const connection = loadConnection();
  const url = validateUrl(process.env.MEM0_OSS_MCP_URL || connection.url);
  const tokenEnvVar = process.env.MEM0_OSS_MCP_TOKEN_ENV_VAR || connection.tokenEnvVar;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenEnvVar)) {
    throw new OpenCodeConnectionConfigError("invalid token environment variable");
  }
  const tokenOverride = process.env[tokenEnvVar]?.trim()
    || process.env[DEFAULT_TOKEN_ENV_VAR]?.trim() || process.env.MEM0_API_KEY?.trim();
  if (!tokenOverride && new URL(url).origin !== new URL(connection.url).origin) {
    throw new OpenCodeConnectionConfigError("a different endpoint origin requires an explicit environment token");
  }
  const envFile = process.env.MEM0_OSS_ENV_FILE || connection.envFile;
  if (envFile && !tokenOverride) {
    try {
      readFileSync(expandHome(envFile), "utf8");
    } catch (error) {
      if (error instanceof Error) throw new OpenCodeConnectionConfigError("cannot read the configured token environment file");
      throw error;
    }
  }
  process.env.MEM0_OSS_MCP_URL = url;
  process.env.MEM0_OSS_MCP_TOKEN_ENV_VAR = tokenEnvVar;
  if (envFile) process.env.MEM0_OSS_ENV_FILE = expandHome(envFile);
  if (tokenOverride) process.env[tokenEnvVar] = tokenOverride;
  if (connection.userId && !process.env.MEM0_USER_ID) process.env.MEM0_USER_ID = connection.userId;
  if (connection.appId && !process.env.MEM0_APP_ID) process.env.MEM0_APP_ID = connection.appId;
  initializeEnvironment({url, tokenEnvVar, envFile, ...options});
  if (!process.env.MEM0_API_KEY?.trim()) {
    throw new OpenCodeConnectionConfigError("the MCP token is not set in the configured environment");
  }
}
