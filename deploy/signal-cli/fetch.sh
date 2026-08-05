#!/usr/bin/env bash
# Fetch and GPG-verify the signal-cli release tarball into this directory.
# The tarball is gitignored (115MB); this script is how you reproduce it.
#
# Upstream publishes NO official Docker image — only tarballs — so building
# from the signed release is the trustworthy path, not a workaround.
set -euo pipefail
VERSION="${1:-0.14.7}"
KEY=FA10826A74907F9EC6BBB7FC2BA2CD21B5B09570   # AsamK <asamk@gmx.de>
cd "$(dirname "$0")"
base="https://github.com/AsamK/signal-cli/releases/download/v${VERSION}"
curl -fsSL -o "signal-cli-${VERSION}.tar.gz"     "${base}/signal-cli-${VERSION}.tar.gz"
curl -fsSL -o "signal-cli-${VERSION}.tar.gz.asc" "${base}/signal-cli-${VERSION}.tar.gz.asc"
gpg --list-keys "$KEY" >/dev/null 2>&1 || \
  gpg --keyserver hkps://keys.openpgp.org --recv-keys "$KEY"
gpg --verify "signal-cli-${VERSION}.tar.gz.asc" "signal-cli-${VERSION}.tar.gz"
echo "verified signal-cli ${VERSION} against ${KEY}"
