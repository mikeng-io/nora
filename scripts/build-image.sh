#!/usr/bin/env bash
# Build Nora's deployment image, stamped with the version it holds.
#
#     scripts/build-image.sh
#
# Two things this wrapper exists to make impossible to forget:
#
#   1. `--network=host`. BuildKit's default bridge cannot resolve
#      deb.debian.org on this host (operational constraint 1), so a plain
#      `docker build` fails in apt-get with a DNS error that reads like a
#      network outage.
#   2. The version stamp (DEC-97, DEC-314). Releases are cut on GitHub by
#      release-please, never here; every build is stamped with
#      `git describe --tags --match 'v[0-9]*'` — `v6.1.0` on the release
#      commit, `v6.1.0-3-gabc1234` past it. A tree that cannot be stamped
#      honestly (dirty, or no release tag reachable yet) builds UNSTAMPED and
#      says so: `version` then answers `unavailable`, which is true.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -ne 0 ]; then
  echo "usage: scripts/build-image.sh (the version comes from git describe)" >&2
  exit 2
fi

# Release tags are made on GitHub, so a checkout that only pulled master may
# not hold the newest one. A failed fetch is not fatal: the stamp is then the
# newest tag this checkout has, which is still a true description of HEAD.
git fetch --quiet --tags origin 2>/dev/null || echo "warning: could not fetch tags; stamping from local tags" >&2

rm -f nora/version.json
VERSION=""
# python3, not `uv run`: the generator is stdlib-only, and `uv` is not on the
# deploy host's non-interactive PATH.
if python3 scripts/gen_version_manifest.py; then
  VERSION="$(python3 -c 'import json; print(json.load(open("nora/version.json"))["version"])')"
else
  echo "warning: building UNSTAMPED (see the refusal above); version answers stay unavailable" >&2
fi
# The commit the image is built from, `+dirty` when the tree is not that
# commit, so the deploy revision never names a sha the code does not match.
SHA="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then SHA="$SHA+dirty"; fi
docker build --pull --network=host --build-arg "NORA_VERSION=$VERSION" \
  --build-arg "NORA_GIT_SHA=$SHA" --build-arg "NORA_BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t nora:latest .
echo "built nora:latest ${VERSION:-unstamped}"
