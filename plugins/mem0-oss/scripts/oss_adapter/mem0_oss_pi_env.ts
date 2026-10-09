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

export function validateRestBaseUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch (error) {
    if (error instanceof TypeError) throw new Mem0ConnectionError("Mem0 OSS base URL must be an absolute http(s) URL");
    throw error;
  }
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname
    || url.username || url.password || url.search || url.hash || url.pathname.replace(/\/+$/, "").endsWith("/mcp")) {
    throw new Mem0ConnectionError("Mem0 OSS base URL must use http(s), without credentials, query, fragment or /mcp");
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
  const baseUrl = validateRestBaseUrl(process.env.MEM0_OSS_BASE_URL || options.url);
  const keyName = process.env.MEM0_OSS_API_KEY_ENV_VAR || options.apiKeyEnvVar;
  const envFile = process.env.MEM0_OSS_ENV_FILE || options.envFile;
  let key = process.env[keyName];
  if (!key && envFile) {
    if (new URL(baseUrl).origin !== new URL(validateRestBaseUrl(options.url)).origin) {
      throw new Mem0ConnectionError("Mem0 OSS endpoint override for a different origin requires an explicit runtime API key");
    }
    for (const line of readFileSync(envFile, "utf8").split(/\r?\n/)) {
      const separator = line.indexOf("=");
      if (separator >= 0 && line.slice(0, separator).trim() === keyName) {
        key = dotenvValue(line.slice(separator + 1));
      }
    }
  }
  process.env.MEM0_OSS_BASE_URL = baseUrl;
  if (key) process.env.MEM0_API_KEY = key;
  process.env.MEM0_TELEMETRY ??= "false";
}
