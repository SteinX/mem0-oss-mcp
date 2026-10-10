#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../../.." && pwd)
version=${1:?Usage: build_pi_release.sh VERSION OUTPUT_DIRECTORY}
output=${2:?Usage: build_pi_release.sh VERSION OUTPUT_DIRECTORY}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Expected a three-part release version' >&2; exit 1; }
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
upstream=${PI_RELEASE_UPSTREAM:-$repo/third_party/mem0}
python3 "$repo/plugins/mem0-oss/scripts/install_pi_plugin.py" --portable --no-build \
  --upstream-plugin-dir "$upstream" --target-root "$stage"
cp "$upstream/LICENSE" "$stage/mem0-oss/LICENSE"
if [ -n "${PI_RELEASE_NODE_MODULES:-}" ]; then
  ln -s "$PI_RELEASE_NODE_MODULES" "$stage/mem0-oss/node_modules"
else
  pnpm --dir "$stage/mem0-oss" install --no-frozen-lockfile --ignore-scripts
fi
cp "$repo/plugins/mem0-oss/scripts/pi_release_tsup.config.ts" "$stage/mem0-oss/tsup.config.ts"
(cd "$stage/mem0-oss" && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/tsup)
python3 "$repo/plugins/mem0-oss/scripts/package_pi_release.py" \
  "$stage/mem0-oss" "$output" "$version" "$(git -C "$repo" rev-parse HEAD)"
