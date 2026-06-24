#!/usr/bin/env bash
# Source this before running official Mem0 hook scripts in generated OSS plugins.

_mem0_oss_read_dotenv_var() {
  _file="$1"
  _name="$2"
  [ -n "$_file" ] || return 0
  [ -f "$_file" ] || return 0
  python3 - "$_file" "$_name" <<'PY'
import shlex
import sys

path, name = sys.argv[1:3]
value = ""
try:
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            if key.strip() != name:
                continue
            try:
                parts = shlex.split(raw_value.strip(), comments=True, posix=True)
            except ValueError:
                parts = []
            value = parts[0] if parts else ""
except OSError:
    pass

if value:
    print(value, end="")
PY
}

_mem0_oss_token_env="${MEM0_OSS_MCP_TOKEN_ENV_VAR:-MEM0_OSS_MCP_TOKEN}"
_mem0_oss_token=""

if command -v printenv >/dev/null 2>&1; then
  _mem0_oss_token="$(printenv "$_mem0_oss_token_env" 2>/dev/null || true)"
fi
if [ -z "$_mem0_oss_token" ] && [ "$_mem0_oss_token_env" != "MEM0_OSS_MCP_TOKEN" ] && [ -n "${MEM0_OSS_MCP_TOKEN:-}" ]; then
  _mem0_oss_token="$MEM0_OSS_MCP_TOKEN"
fi
if [ -z "$_mem0_oss_token" ] && [ -n "${MEM0_OSS_ENV_FILE:-}" ]; then
  _mem0_oss_token="$(_mem0_oss_read_dotenv_var "$MEM0_OSS_ENV_FILE" "$_mem0_oss_token_env")"
  if [ -z "$_mem0_oss_token" ] && [ "$_mem0_oss_token_env" != "MEM0_OSS_MCP_TOKEN" ]; then
    _mem0_oss_token="$(_mem0_oss_read_dotenv_var "$MEM0_OSS_ENV_FILE" "MEM0_OSS_MCP_TOKEN")"
  fi
  if [ -z "$_mem0_oss_token" ]; then
    _mem0_oss_token="$(_mem0_oss_read_dotenv_var "$MEM0_OSS_ENV_FILE" "MEM0_API_KEY")"
  fi
fi
if [ -z "$_mem0_oss_token" ] && [ -n "${MEM0_API_KEY:-}" ]; then
  _mem0_oss_token="$MEM0_API_KEY"
fi

if [ -n "$_mem0_oss_token" ]; then
  MEM0_API_KEY="$_mem0_oss_token"
  MEM0_OSS_MCP_TOKEN="${MEM0_OSS_MCP_TOKEN:-$_mem0_oss_token}"
  export MEM0_API_KEY MEM0_OSS_MCP_TOKEN
fi

export MEM0_OSS_MCP_TOKEN_ENV_VAR="$_mem0_oss_token_env"
export MEM0_TELEMETRY="${MEM0_TELEMETRY:-false}"
