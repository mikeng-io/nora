"""Nothing fetched through a shared file link is ever cached.

A linked file is LIVE: its content, name, kind, owner and modified time all
change, unlike an attachment's bytes, which are fixed when they arrive. So the
external-files code keeps the parsed URL ref and nothing else, and every open
fetches fresh. This reads the source of every external-files module, every
adapter under `nora/infra/external_files/` included, and fails on an
import of anything that keeps what a read returned: the blob store, the media
object or description stores, the media join, or a memoizing cache.

A static check, `test_media_join_reaches_every_transcript`'s shape: the
property is «nothing here can keep a read», which no single behavioural test
of one open can show.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The external-files modules: the domain module, the ingest stage, the fake,
#: and every adapter under `nora/infra/external_files/` (the directory is also
#: scanned whole, so a new adapter is covered unnamed).
_MODULES = (
    "nora/domain/external_files.py",
    "nora/ingest/stages/external_files.py",
    "nora/testing/external_files.py",
    "nora/infra/external_files/google_drive.py",
    "nora/infra/external_files/text.py",
    "nora/infra/external_files/parse.py",
)
_ADAPTER_DIR = ROOT / "nora" / "infra" / "external_files"

#: Modules that KEEP what was read: custody and its indexes, and caches.
_FORBIDDEN_MODULES = (
    "nora.infra.blobs", "nora.domain.media_join", "nora.ingest.comprehension",
    "nora.infra.sql.media", "cachetools", "diskcache", "aiocache",
)
#: Names that keep a read: the store ports and the stdlib memoizers.
_FORBIDDEN_NAMES = frozenset({
    "BlobStore", "MediaObjectStore", "MediaDescriptionStore", "VideoReadingStore",
    "MediaJoin", "lru_cache", "cache", "cached_property",
})


def _paths() -> list[Path]:
    paths = [ROOT / m for m in _MODULES]
    if _ADAPTER_DIR.is_dir():
        paths += sorted(_ADAPTER_DIR.rglob("*.py"))
    return paths


def _offences(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [
                a.name for a in node.names
                if a.name.startswith(_FORBIDDEN_MODULES) or a.name == "functools"
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(_FORBIDDEN_MODULES):
                found.append(module)
            found += [
                f"{module}.{a.name}" for a in node.names if a.name in _FORBIDDEN_NAMES
            ]
    return found


def test_the_external_files_modules_exist():
    """The positive control: a renamed module would otherwise leave this
    gate checking nothing."""
    missing = [m for m in _MODULES if not (ROOT / m).is_file()]
    assert not missing, f"{missing} are gone: point _MODULES at their new names"


def test_no_external_files_module_can_keep_what_it_read():
    offences = {
        p.relative_to(ROOT).as_posix(): o
        for p in _paths() if (o := _offences(p.read_text(encoding="utf-8")))
    }
    assert not offences, (
        f"{offences}: a linked file is live and is never cached (content, bytes "
        "or metadata). Fetch fresh on every open; a copy to her computer is a "
        "snapshot labelled with its modified time, not a cache."
    )


def test_the_check_sees_a_cache_when_there_is_one():
    """The gate's own control: it must fire on each shape it exists for."""
    for source in (
        "from functools import lru_cache\n",
        "import functools\n",
        "from nora.domain.ports import BlobStore\n",
        "from nora.domain.media_join import MediaJoin\n",
        "import cachetools\n",
    ):
        assert _offences(source), source
    assert not _offences("from nora.domain.media import HANDLE_CHARS\n")
