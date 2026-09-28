#!/usr/bin/env bash
# Build Nora's ordinary, unversioned deployment image.
#
#     scripts/build-image.sh
#
# Two things this wrapper exists to make impossible to forget:
#
#   1. `--network=host`. BuildKit's default bridge cannot resolve
#      deb.debian.org on this host (operational constraint 1), so a plain
#      `docker build` fails in apt-get with a DNS error that reads like a
#      network outage.
#   2. Deployments are not releases. This always removes any version stamp and
#      builds `nora:latest` without an announcement. `scripts/release.py` is
#      the sole explicit release command; it invokes `build-release-image.sh`
#      only after the operator has prepared and approved a release record.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -ne 0 ]; then
  echo "deployments are unversioned; use scripts/release.py for an explicit release" >&2
  exit 2
fi

rm -f nora/version.json
# The commit the image is built from, `+dirty` when the tree is not that
# commit, so the deploy revision never names a sha the code does not match.
SHA="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then SHA="$SHA+dirty"; fi
docker build --network=host --build-arg NORA_VERSION= \
  --build-arg "NORA_GIT_SHA=$SHA" --build-arg "NORA_BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t nora:latest .
echo "built unversioned nora:latest"
