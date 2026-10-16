"""scripts/migrate_media_to_rustfs.py — the one-time filesystem-to-RustFS
copy (DEC-136).

Exercised against a REAL filesystem directory and a REAL rustfs container
(`tests/conftest.py`'s `rustfs_client`/`rustfs_container`, the same fixtures
`tests/contract/test_blob_store_contract.py` uses) — not a mock of either
side, so this proves the actual copy round-trips through both real stores.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from nora.infra.blobs import FilesystemBlobStore, RustfsBlobStore
from nora.infra.blobs.cipher import BlobCipher
from scripts.migrate_media_to_rustfs import _iter_keys, main

KEY = base64.b64encode(bytes(range(32))).decode()
PAYLOADS = [b"first blob", b"second, a bit longer blob", b""]


@pytest.fixture
def media_root(tmp_path):
    return tmp_path / "data" / "media"


@pytest.fixture
async def seeded_source(media_root):
    """A real FilesystemBlobStore, pre-populated the way production's own
    ingest pipeline populates it — through `put`, never by hand-writing
    files, so the on-disk shape this test seeds is provably the shape the
    real store produces."""
    store = FilesystemBlobStore(media_root, cipher=BlobCipher.from_base64(KEY))
    keys = [await store.put(data=p) for p in PAYLOADS]
    return keys


@pytest.fixture
def config_toml(tmp_path, media_root):
    """`nora.config.load` validates the WHOLE `Config` model, not just
    `[store]`/`[media]` — a hand-rolled minimal TOML is missing a dozen
    unrelated required fields. `config.example.toml` is already a complete,
    valid file (the same base `tests/test_composition.py`'s own `cfg`
    fixture copies), so this only overrides `store.data_dir` — the one
    thing that has to point at this test's own throwaway directory rather
    than the real deployment's."""
    import shutil

    root = Path(__file__).resolve().parents[1]
    path = tmp_path / "config.toml"
    shutil.copy(root / "config.example.toml", path)
    text = path.read_text()
    marker = 'data_dir = "~/workspace/nora/data"'
    assert marker in text, "config.example.toml's [store].data_dir moved"
    path.write_text(text.replace(marker, f'data_dir = "{media_root.parent}"'))
    return path


@pytest.fixture
def rustfs_bucket_env(monkeypatch, rustfs_container):
    from tests.conftest import RUSTFS_TEST_PASSWORD, RUSTFS_TEST_USER

    monkeypatch.setenv(
        "RUSTFS_ENDPOINT",
        f"http://{rustfs_container.get_container_host_ip()}:"
        f"{rustfs_container.get_exposed_port(9000)}",
    )
    monkeypatch.setenv("RUSTFS_BUCKET", "migration-test")
    monkeypatch.setenv("RUSTFS_ROOT_USER", RUSTFS_TEST_USER)
    monkeypatch.setenv("RUSTFS_ROOT_PASSWORD", RUSTFS_TEST_PASSWORD)
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", KEY)


def test_iter_keys_reads_back_exactly_what_put_wrote(media_root, seeded_source):
    found = set(_iter_keys(media_root))
    assert found == set(seeded_source)


def test_iter_keys_on_a_missing_root_yields_nothing(tmp_path):
    """The root doesn't exist yet on a deployment with no media custody at
    all — an empty result, not a crash."""
    assert list(_iter_keys(tmp_path / "never-created")) == []


async def test_migration_copies_every_blob_and_reads_back_correctly(
    config_toml, seeded_source, rustfs_bucket_env, monkeypatch, rustfs_client,
):
    import scripts.migrate_media_to_rustfs as migrate

    monkeypatch.setattr(migrate, "CONFIG_PATH", config_toml)
    exit_code = await main()
    assert exit_code == 0

    dest = RustfsBlobStore(
        s3_client=rustfs_client, bucket="migration-test",
        cipher=BlobCipher.from_base64(KEY),
    )
    for key, payload in zip(seeded_source, PAYLOADS, strict=True):
        assert await dest.get(key=key) == payload


async def test_migration_is_idempotent(
    config_toml, seeded_source, rustfs_bucket_env, monkeypatch, capsys,
):
    """A re-run over the same source must not fail or double-copy — the
    exact "meme forwarded five times" property the port's own contract
    suite already requires of each store individually, now proven across a
    full migration run."""
    import scripts.migrate_media_to_rustfs as migrate

    monkeypatch.setattr(migrate, "CONFIG_PATH", config_toml)
    first = await main()
    second = await main()
    assert first == 0
    assert second == 0
    out = capsys.readouterr().out
    assert f"copied {len(seeded_source)}/{len(seeded_source)}" in out


async def test_a_missing_encryption_key_refuses_rather_than_guesses(
    config_toml, media_root, rustfs_bucket_env, monkeypatch,
):
    monkeypatch.delenv("MEDIA_ENCRYPTION_KEY", raising=False)
    import scripts.migrate_media_to_rustfs as migrate

    monkeypatch.setattr(migrate, "CONFIG_PATH", config_toml)
    assert await main() == 2


async def test_missing_rustfs_credentials_refuse_rather_than_guess(
    config_toml, monkeypatch,
):
    for var in ("RUSTFS_ENDPOINT", "RUSTFS_ROOT_USER", "RUSTFS_ROOT_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEDIA_ENCRYPTION_KEY", KEY)
    import scripts.migrate_media_to_rustfs as migrate

    monkeypatch.setattr(migrate, "CONFIG_PATH", config_toml)
    assert await main() == 2
