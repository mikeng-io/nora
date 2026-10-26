"""`materialise` writes what was in force at T for a restore: the identity,
policy and doctrine it names are private. Its export directory is 0700, every
file in it is 0600 whatever the umask, and a link or an existing directory
that is not private at the output path is refused rather than written through.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from nora.app.revisions import RevisionRecorder
from nora.doctrine.loader import DoctrineStore
from nora.infra.sql import create_sql_engine
from nora.infra.sql.revisions import SqlRevisionStore
from nora.infra.sql.snapshot_at import SnapshotRefusedError, materialise
from nora.instructions import DreamerInstructions
from nora.obs.metrics import Metrics

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _recorded(engine: Any, tmp_path: Path) -> Any:
    """One load of every corpus and the config files, recorded by T."""
    doctrine_dir = tmp_path / "doctrine"
    dreamer_dir = tmp_path / "dreamer"
    shutil.copytree(REPO / "nora" / "doctrine", doctrine_dir)
    shutil.copytree(REPO / "nora" / "dreamer", dreamer_dir)
    config = tmp_path / "config.toml"
    config.write_text("# a config\n", encoding="utf-8")
    policy = tmp_path / "policy.toml"
    policy.write_text("# a policy\n", encoding="utf-8")
    metrics = Metrics()
    recorder = RevisionRecorder(
        store=SqlRevisionStore(engine),
        doctrine=DoctrineStore(doctrine_dir, metrics=metrics),
        dreamer=DreamerInstructions(dreamer_dir, metrics=metrics),
        config_files=(("config.toml", config), ("policy.toml", policy)),
        metrics=metrics, manifest_path=tmp_path / "absent-version.json",
        hostname="test-host", environ={"NORA_GIT_SHA": "0" * 40},
    )
    await recorder.record_start(started_at=await _clock(engine))
    return recorder


async def _clock(engine: Any) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


async def test_the_export_is_private_whatever_the_umask(engine: Any, tmp_path: Path) -> None:
    await _recorded(engine, tmp_path)
    out = tmp_path / "restored"
    old = os.umask(0o022)
    try:
        _forced, written = await materialise(engine, await _clock(engine), out)
    finally:
        os.umask(old)
    assert _mode(out) == 0o700
    assert (out / "in_force.json") in written
    for path in written:
        assert _mode(path) == 0o600, path


async def test_an_existing_export_directory_that_others_can_read_is_refused(
    engine: Any, tmp_path: Path,
) -> None:
    await _recorded(engine, tmp_path)
    out = tmp_path / "shared"
    out.mkdir(mode=0o755)
    os.chmod(out, 0o755)  # noqa: S103 — the directory is deliberately not private
    with pytest.raises(SnapshotRefusedError, match="only this account"):
        await materialise(engine, await _clock(engine), out)
    assert not (out / "in_force.json").exists()


async def test_a_link_at_an_output_path_is_refused_and_its_target_untouched(
    engine: Any, tmp_path: Path,
) -> None:
    await _recorded(engine, tmp_path)
    out = tmp_path / "restored"
    out.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    (out / "in_force.json").symlink_to(victim)
    with pytest.raises(SnapshotRefusedError):
        await materialise(engine, await _clock(engine), out)
    assert victim.read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.parametrize("kind", ["hardlink", "directory-link", "existing-file"])
async def test_existing_export_contents_are_never_overwritten(
    engine: Any, tmp_path: Path, kind: str,
) -> None:
    await _recorded(engine, tmp_path)
    out = tmp_path / "restored"
    out.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n")
    if kind == "hardlink":
        os.link(victim, out / "in_force.json")
    elif kind == "directory-link":
        (out / "doctrine").symlink_to(tmp_path / "doctrine", target_is_directory=True)
    else:
        (out / "in_force.json").write_text("keep this too\n")
    with pytest.raises(SnapshotRefusedError):
        await materialise(engine, await _clock(engine), out)
    assert victim.read_text() == "keep me\n"
    if kind == "existing-file":
        assert (out / "in_force.json").read_text() == "keep this too\n"
