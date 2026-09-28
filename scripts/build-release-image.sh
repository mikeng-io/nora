#!/usr/bin/env bash
# Build a versioned image only for the explicit manual release command.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/build-release-image.sh V<n>" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
VERSION="$1"
# python3, not `uv run`: the generator is stdlib-only, and `uv` is not on the
# deploy host's non-interactive PATH, which is where a release runs.
python3 scripts/gen_version_manifest.py --version "$VERSION"
docker build --network=host --build-arg "NORA_VERSION=$VERSION" \
  --build-arg "NORA_GIT_SHA=$(git rev-parse HEAD)" \
  --build-arg "NORA_BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t nora:"$VERSION" -t nora:latest .
echo "built release nora:$VERSION"
