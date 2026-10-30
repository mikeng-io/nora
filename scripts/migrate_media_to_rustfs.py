"""One-time copy of every blob already on the filesystem into RustFS.

Run from inside the `nora` container, AFTER RustFS is reachable but BEFORE
`[media.rustfs].enabled` is flipped to true (DEC-136) — this script only
COPIES; it never touches or deletes the filesystem originals, and cutting
the running store over to RustFS is a separate step (flip the flag, restart).

Content-addressed, so idempotent and safe to re-run: every key is the
sha256 of its own plaintext, and a re-run just re-uploads the same bytes
under the same key. A per-blob failure is logged and counted, not fatal —
the run reports what it could not copy rather than stopping over one bad
file, the same posture `nora.ingest.stages.media._take` already takes.

Usage (the `deploy/smoke_giphy_signal.py` pattern — copy in, run inside):
    docker cp scripts/migrate_media_to_rustfs.py nora:/tmp/migrate.py
    python3 ~/workspace/nora/scripts/secret_delivery.py \\
        --runtime-root ~/workspace/nora --compose-exec -T \\
        -e RUSTFS_ENDPOINT=http://rustfs:9000 \\
        -e RUSTFS_BUCKET=nora-media \\
        nora uv run --no-sync python /tmp/migrate.py

RUSTFS_ROOT_USER/RUSTFS_ROOT_PASSWORD are already in the container's own
environment (docker-compose.yml passes them through) and need no extra
flag. The filesystem side (data_dir, encryption_key_env) is read from the
real config.toml already mounted at /app/config.toml — the same source of
truth the daemon itself uses, so this script cannot silently migrate the
wrong directory or seal under the wrong key.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import boto3

from nora.config import load
from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.secret import credentials

CONFIG_PATH = Path("/app/config.toml")


def _iter_keys(media_root: Path) -> Iterator[str]:
    """Every sha256 key this store could have produced, read off its own
    `<first2>/<rest>` sharding — the inverse of
    `FilesystemBlobStore._path_for`. Anything that does not fit that shape
    (a stray file, a `.tmp` left by a crashed write) is skipped rather than
    guessed at."""
    if not media_root.is_dir():
        return
    for shard in sorted(media_root.iterdir()):
        if not shard.is_dir() or len(shard.name) != 2:
            continue
        for blob in sorted(shard.iterdir()):
            key = shard.name + blob.name
            if len(key) == 64 and all(c in "0123456789abcdef" for c in key):
                yield key


async def main() -> int:
    endpoint = credentials().get("RUSTFS_ENDPOINT", "")
    bucket = credentials().get("RUSTFS_BUCKET", "nora-media")
    access_key = credentials().get("RUSTFS_ROOT_USER", "")
    secret_key = credentials().get("RUSTFS_ROOT_PASSWORD", "")
    if not endpoint or not access_key or not secret_key:
        print(
            "RUSTFS_ENDPOINT, RUSTFS_ROOT_USER and RUSTFS_ROOT_PASSWORD are all "
            "required",
            file=sys.stderr,
        )
        return 2

    cfg = load(CONFIG_PATH, require_secrets=False)
    if not cfg.media.encryption_key_env:
        print("media.encryption_key_env is unset in config.toml", file=sys.stderr)
        return 2
    key_b64 = credentials().get(cfg.media.encryption_key_env, "")
    if not key_b64:
        print(f"{cfg.media.encryption_key_env} is not set", file=sys.stderr)
        return 2
    cipher = BlobCipher.from_base64(key_b64)

    media_root = cfg.store.data_dir / "media"
    source = FilesystemBlobStore(media_root, cipher=cipher)

    s3 = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
    )
    dest = RustfsBlobStore(s3_client=s3, bucket=bucket, cipher=cipher)
    await asyncio.to_thread(dest.ensure_bucket)

    keys = list(_iter_keys(media_root))
    print(f"found {len(keys)} blobs under {media_root}")

    copied = 0
    failed: list[str] = []
    for key in keys:
        try:
            plaintext = await source.get(key=key)
            if plaintext is None:
                # Listed from the same directory a moment ago; None here means
                # the listing and the store disagree (a concurrent delete),
                # not that this blob was never real.
                raise RuntimeError("filesystem store reports absent")
            written = await dest.put(data=plaintext)
            if written != key:
                raise RuntimeError(f"wrote under {written}, not the source key")
        except Exception as exc:  # noqa: BLE001 — a per-blob failure is reported, not fatal
            failed.append(key)
            print(f"  FAILED {key}: {exc}", file=sys.stderr)
            continue
        copied += 1

    print(f"copied {copied}/{len(keys)}")
    if failed:
        print(f"{len(failed)} blob(s) failed — see stderr above", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
