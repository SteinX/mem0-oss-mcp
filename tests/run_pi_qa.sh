#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
upstream=${PI_QA_UPSTREAM:-"$repo/third_party/mem0"}
qa_root=$(mktemp -d "${TMPDIR:-/tmp}/pi-mcp-qa.XXXXXX")
trap 'rm -rf "$qa_root"; if [ -L "$repo/node_modules" ] && [ "$(readlink "$repo/node_modules")" = "$qa_root/runtime/node_modules" ]; then rm "$repo/node_modules"; fi' EXIT
mkdir "$qa_root/runtime"
cp "$repo/tests/pi-qa/package.json" "$qa_root/runtime/package.json"
pnpm --dir "$qa_root/runtime" install --no-frozen-lockfile --ignore-scripts
if [ ! -e "$repo/node_modules" ]; then ln -s "$qa_root/runtime/node_modules" "$repo/node_modules"; fi
export PI_QA_ROOT="$qa_root"
export PI_QA_PLUGIN="$qa_root/packages/mem0-oss"
python3 "$repo/plugins/mem0-oss/scripts/install_pi_plugin.py" --url https://fixture.test/mcp \
  --upstream-plugin-dir "$upstream" --target-root "$qa_root/packages" --pi-dir "$qa_root/agent" --no-build --install
ln -s "$qa_root/runtime/node_modules" "$PI_QA_PLUGIN/node_modules"
"$qa_root/runtime/node_modules/.bin/tsc" --noEmit -p "$PI_QA_PLUGIN/tsconfig.json"
bun "$repo/tests/pi_mcp_client_qa.ts"
bun "$repo/tests/pi_delete_qa.ts"
bun "$repo/tests/pi_connection_qa.ts"
bun "$repo/tests/pi_runtime_qa.ts"
bun "$repo/tests/pi_senpi_preview_qa.ts"
if [ -n "${PI_QA_SIDECAR_SOURCE:-}" ]; then python3 "$repo/tests/pi_chain_qa.py"; fi
(cd "$PI_QA_PLUGIN" && "$qa_root/runtime/node_modules/.bin/tsup")
python3 - "$PI_QA_PLUGIN/package.json" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
package = json.loads(path.read_text())
package["pi"]["extensions"] = ["./dist/entry.js"]
path.write_text(json.dumps(package))
PY
bun "$repo/tests/pi_runtime_qa.ts"
bun "$repo/tests/pi_senpi_preview_qa.ts"
bun "$repo/tests/pi_cli_qa.ts"
node "$qa_root/runtime/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js" --help >/dev/null
node "$qa_root/runtime/node_modules/@code-yeongyu/senpi/dist/bundle/cli.js" --help >/dev/null
