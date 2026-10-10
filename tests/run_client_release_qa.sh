#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
assets=${1:?Usage: run_client_release_qa.sh ASSET_DIRECTORY VERSION}
version=${2:?Usage: run_client_release_qa.sh ASSET_DIRECTORY VERSION}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp "$assets"/mem0-oss-{codex,opencode}-"$version".{zip,sha256} "$stage/"
python3 "$repo/plugins/mem0-oss/scripts/build_client_releases.py" 9.9.9 "$stage" \
  --upstream "${CLIENT_RELEASE_UPSTREAM:-$repo/third_party/mem0}"
if [ -z "${CLIENT_RELEASE_QA_NODE_MODULES:-}" ]; then
  mkdir -p "$stage/runtime"
  bun add --cwd "$stage/runtime" --trust opencode-ai@1.15.11 @openai/codex@0.160.1 zod@4.6.5
  runtime="$stage/runtime/node_modules"
else
  runtime=$CLIENT_RELEASE_QA_NODE_MODULES
fi
cp "$repo/tests/client_release_cli_qa.ts" "$stage/client_release_cli_qa.ts"
ln -s "$runtime" "$stage/node_modules"
unzip -q "$stage/mem0-oss-codex-$version.zip" -d "$stage/extracted"
CLIENT_RELEASE_QA_INSTALLER="$stage/extracted/mem0-oss/install.py" \
  PATH="$runtime/.bin:$PATH" bun "$stage/client_release_cli_qa.ts" "$stage" "$version" "$stage/native"
