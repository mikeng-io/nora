"""Operator one-shot: put Nora's canonical self picture into her media library.

DEC-193. The `image-generation` skill names her
canonical picture by its full sha256; `generate_image`'s resolver lets exactly
one kind of row resolve outside its own conversation, the one whose
`provenance.role` is `"self"`. This script is the ONLY writer of that marker,
and it runs at the trust level of the skill file itself: the operator, on the
deploy host. Nothing a message can reach writes `provenance.role`.

What it does, in order, and why each step fails closed:

1. Reads the handle out of the SHIPPED skill and refuses bytes whose sha256 is
   not that handle. The skill and the library can then never disagree silently:
   a re-rendered image with new bytes is refused here instead of leaving the
   skill naming a picture nobody holds.
2. `blob_store.put`, checking the key it returns is that same sha.
3. `media_store.save` (first-source-wins: a no-op if the bytes are already
   indexed, e.g. somebody once posted the same picture in a room), then
   `media_store.comprehend`, whose upsert MERGES `provenance` — so an existing
   row is marked too, its birth record kept. No new store verb: both are the
   port's own, contract-tested against the SQL store.
4. Reads the row back and requires `role == "self"`. An unreachable index
   answers `False`/`None`, which is a failed ingest, never a quiet success.

Idempotent: re-running re-puts the same key and merges the same marker.

Usage, on the deploy host, AFTER the image carrying the skill is running
(`nora/skills` is baked into the image, so the handle is read from the
container's own copy):

    docker cp scripts/ingest_self_asset.py nora:/tmp/ingest_self_asset.py
    docker cp assets/nora-canonical-self.png nora:/tmp/nora-canonical-self.png
    docker compose exec -T nora uv run --no-sync python \\
        /tmp/ingest_self_asset.py /tmp/nora-canonical-self.png

The database URL, the media key and the blob backend are read exactly as the
daemon reads them (`/app/config.toml` plus the container environment), and the
blob store is built by the daemon's own builder, so the bytes land where the
running resolver looks.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from nora.domain.media import MediaObject
from nora.domain.ports import BlobStore, MediaObjectStore
from nora.secret import credentials

#: The marker the resolver's exemption reads. Only `role` is load-bearing (an
#: exact string match); the other two say where it came from.
SELF_PROVENANCE: dict[str, object] = {
    "role": "self", "source": "operator-provided", "canonical": True,
}
TITLE = "Nora, canonical self"
DESCRIPTION = (
    "Nora's canonical self picture: Mini Nora in 2D anime Bauhinia styling, "
    "full body, front facing, with a compact dark-brown bun and flower clasp, "
    "an ivory blouse, navy waistcoat, open grey coat, wide navy trousers and "
    "ivory boots. No bag or audio accessories."
)
DESCRIBE_MODEL = "operator-provided"

CONFIG_PATH = Path("/app/config.toml")
SKILL_PATH = Path("/app/nora/skills/image-generation/SKILL.md")

_HANDLE_RE = re.compile(r"\[image ([0-9a-f]{64})\]")


class IngestRefusedError(Exception):
    """The ingest could not establish the canonical row; nothing is claimed."""


def skill_self_handle(skill: Path) -> str:
    """The one full-sha `[image …]` handle the skill carries, or refuse."""
    found = set(_HANDLE_RE.findall(skill.read_text(encoding="utf-8")))
    if len(found) != 1:
        raise IngestRefusedError(
            f"{skill} must carry exactly one full-sha [image …] handle, found "
            f"{len(found)}"
        )
    return found.pop()


async def ingest_self(
    *, data: bytes, expected_sha256: str, blob_store: BlobStore,
    media_store: MediaObjectStore,
) -> MediaObject:
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != expected_sha256:
        raise IngestRefusedError(
            f"these bytes are {sha256}, the skill names {expected_sha256}: "
            "re-pin the skill or ingest the image it names"
        )
    key = await blob_store.put(data=data)
    if key != sha256:
        raise IngestRefusedError(f"blob store wrote {key}, not {sha256}")
    await media_store.save(obj=MediaObject(
        sha256=sha256, media_type="image/png", size=len(data), source="fetched",
        title=TITLE, description=DESCRIPTION, describe_model=DESCRIBE_MODEL,
        provenance=dict(SELF_PROVENANCE),
        created_at_ms=int(datetime.now(UTC).timestamp() * 1000),
    ))
    marked = await media_store.comprehend(
        sha256=sha256, title=TITLE, description=DESCRIPTION, content="",
        model=DESCRIBE_MODEL, provenance=dict(SELF_PROVENANCE),
    )
    row = await media_store.get(sha256=sha256)
    if not marked or row is None or (row.provenance or {}).get("role") != "self":
        raise IngestRefusedError(f"media index did not read back {sha256} as role=self")
    return row


async def _main(argv: list[str]) -> int:
    from nora.app.composition import _build_blob_store
    from nora.config import load
    from nora.infra.blobs.cipher import BlobCipher
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.media_objects import SqlMediaObjectStore

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--skill", type=Path, default=SKILL_PATH)
    args = parser.parse_args(argv)

    cfg = load(args.config, require_secrets=False)
    key_b64 = credentials().get(cfg.media.encryption_key_env or "", "")
    if not key_b64:
        print("media encryption key is not set; custody is off", file=sys.stderr)
        return 2
    database_url = credentials().get(cfg.store.database_url_env, "")
    if not database_url:
        print(f"{cfg.store.database_url_env} is not set", file=sys.stderr)
        return 2
    blob_store = await _build_blob_store(cfg, cipher=BlobCipher.from_base64(key_b64))
    engine = create_sql_engine(database_url)
    try:
        row = await ingest_self(
            data=args.image.read_bytes(),
            expected_sha256=skill_self_handle(args.skill),
            blob_store=blob_store, media_store=SqlMediaObjectStore(engine),
        )
    except IngestRefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"ok: {row.sha256} role={row.provenance.get('role')} source={row.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
