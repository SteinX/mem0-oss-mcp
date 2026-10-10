import { readFileSync } from "node:fs";

interface ConnectionOptions {
  readonly url: string;
  readonly apiKeyEnvVar: string;
  readonly envFile: string | undefined;
}

class Mem0ConnectionError extends Error {
  constructor(readonly reason: string) {
    super(reason);
    this.name = "Mem0ConnectionError";
  }
}

export function validateMcpUrl(value: string): string {
  const raw = value.trim();
  if (!/^https?:\/\//i.test(raw) || /[\\\x00-\x1f\x7f]/.test(raw)) {
    throw new Mem0ConnectionError("Mem0 OSS MCP URL must be an absolute http(s) URL");
  }
  let url: URL;
  try {
    url = new URL(raw);
  } catch (error) {
    if (error instanceof TypeError) throw new Mem0ConnectionError("Mem0 OSS MCP URL must be an absolute http(s) URL");
    throw error;
  }
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname
    || url.username || url.password || url.search || url.hash
    || !url.pathname.replace(/\/+$/, "").endsWith("/mcp")) {
    throw new Mem0ConnectionError("Mem0 OSS MCP URL must use http(s), without credentials, query or fragment, ending in /mcp");
  }
  return url.href.replace(/\/+$/, "");
}

function dotenvValue(raw: string): string {
  let result = "";
  let quote: string | undefined;
  let escaped = false;
  const value = raw.trim();
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (escaped) {
      result += char;
      escaped = false;
    } else if (quote === "'") {
      if (char === "'") quote = undefined;
      else result += char;
    } else if (char === "\\") {
      escaped = true;
    } else if (quote === '"') {
      if (char === '"') quote = undefined;
      else result += char;
    } else if (char === "'" || char === '"') {
      quote = char;
    } else if (/\s/.test(char) && /^\s+#/.test(value.slice(index))) {
      break;
    } else {
      result += char;
    }
  }
  if (escaped) result += "\\";
  return result;
}

export function initializeMem0OssEnv(options: ConnectionOptions): void {
  const baseUrl = validateMcpUrl(process.env.MEM0_OSS_MCP_URL || options.url);
  const keyName = process.env.MEM0_OSS_MCP_TOKEN_ENV_VAR || options.apiKeyEnvVar;
  const envFile = process.env.MEM0_OSS_ENV_FILE || options.envFile;
  let key = process.env[keyName];
  if (!key && new URL(baseUrl).origin !== new URL(validateMcpUrl(options.url)).origin) {
    throw new Mem0ConnectionError("Mem0 OSS endpoint override for a different origin requires an explicit runtime API key");
  }
  if (!key && envFile) {
    for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
      const separator = line.indexOf("=");
      if (separator >= 0 && line.slice(0, separator).trim() === keyName) {
        key = dotenvValue(line.slice(separator + 1));
      }
    }
  }
  key ||= process.env.MEM0_API_KEY;
  process.env.MEM0_OSS_PI_RESOLVED_MCP_URL = baseUrl;
  if (key) process.env.MEM0_OSS_PI_RESOLVED_API_KEY = key;
  else delete process.env.MEM0_OSS_PI_RESOLVED_API_KEY;
  process.env.MEM0_TELEMETRY ??= "false";
}
