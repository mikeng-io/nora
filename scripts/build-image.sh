#!/usr/bin/env bash
# Build Nora's image with the version baked in.
#
#     scripts/build-image.sh V3            # stamp V3 and build
#     scripts/build-image.sh               # stamp the newest V-tag on HEAD
#
# Two things this wrapper exists to make impossible to forget:
#
#   1. `--network=host`. BuildKit's default bridge cannot resolve
#      deb.debian.org on this host (operational constraint 1), so a plain
#      `docker build` fails in apt-get with a DNS error that reads like a
#      network outage.
#   2. The version stamp. `nora/version.json` is generated HERE, on the host,
#      because .dockerignore excludes .git/ and the container has no git — and
#      the same version is passed as --build-arg so the in-image gate
#      (scripts/check_version_stamp.py) can refuse a build whose stamp and
#      build argument disagree.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [ -n "$VERSION" ]; then
  uv run python scripts/gen_version_manifest.py --version "$VERSION"
else
  uv run python scripts/gen_version_manifest.py
  VERSION="$(python3 -c 'import json;print(json.load(open("nora/version.json"))["version"])')"
fi

docker build --network=host --build-arg "NORA_VERSION=$VERSION" -t nora:"$VERSION" -t nora:latest .
echo "built nora:$VERSION"
