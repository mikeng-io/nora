#!/usr/bin/env bash
# Build a versioned image only for the explicit manual release command.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/build-release-image.sh V<n>" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
VERSION="$1"
uv run python scripts/gen_version_manifest.py --version "$VERSION"
docker build --network=host --build-arg "NORA_VERSION=$VERSION" -t nora:"$VERSION" -t nora:latest .
echo "built release nora:$VERSION"
