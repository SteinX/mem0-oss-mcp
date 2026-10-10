#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
assets=${1:?Usage: run_pi_release_qa.sh ASSETS_DIRECTORY VERSION}
version=${2:?Usage: run_pi_release_qa.sh ASSETS_DIRECTORY VERSION}
qa=$(mktemp -d)
trap 'rm -rf "$qa"' EXIT
mkdir "$qa/extracted" "$qa/runtime"
cp "$repo/tests/pi-qa/package.json" "$qa/runtime/package.json"
if [ -n "${PI_RELEASE_NODE_MODULES:-}" ]; then
  runtime=$PI_RELEASE_NODE_MODULES
else
  pnpm --dir "$qa/runtime" install --no-frozen-lockfile --ignore-scripts
  runtime=$qa/runtime/node_modules
fi
unzip "$assets/mem0-oss-pi-$version.zip" -d "$qa/extracted"
PI_RELEASE_QA_ROOT="$qa/agent" PI_RELEASE_QA_PLUGIN="$qa/extracted/mem0-oss" \
  PI_RELEASE_NODE_MODULES="$runtime" bun "$repo/tests/pi_release_cli_qa.ts"
