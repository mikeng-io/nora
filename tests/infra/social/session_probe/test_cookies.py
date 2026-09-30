"""Decrypt round-trip on a v10 blob this test encrypts itself, plus the jar reader."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from nora.infra.social.session_probe import cookies as ck

KEY = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, dklen=16)


def encrypt_v10(plain: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    enc = Cipher(algorithms.AES(KEY), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + enc.update(padded) + enc.finalize()


def webkit(dt: datetime) -> int:
    return int((dt - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 1_000_000)


def test_decrypt_round_trip_plain() -> None:
    assert ck.decrypt_value(".instagram.com", encrypt_v10(b"abc%3Adef")) == "abc%3Adef"


def test_decrypt_strips_the_host_hash_prefix_only_when_it_matches() -> None:
    host = ".instagram.com"
    digest = hashlib.sha256(host.encode()).digest()
    assert ck.decrypt_value(host, encrypt_v10(digest + b"secret")) == "secret"
    # 32 bytes that are not this host's digest are data, not a prefix to strip
    lookalike = b"x" * 32 + b"z"
    assert ck.decrypt_value(host, encrypt_v10(lookalike)) == lookalike.decode()


def test_v11_and_garbage_are_refused_without_echoing_bytes() -> None:
    with pytest.raises(ck.CookieError, match="v11"):
        ck.decrypt_value(".x.com", b"v11" + b"\x00" * 16)
    with pytest.raises(ck.CookieError):
        ck.decrypt_value(".x.com", b"v10" + b"\x01" * 7)
    with pytest.raises(ck.CookieError):
        ck.decrypt_value(".x.com", b"v10" + b"\x01" * 32)  # bad padding


def make_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, "
        "creation_utc INTEGER, expires_utc INTEGER)"
    )
    con.executemany("INSERT INTO cookies VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_read_cookies_decrypts_filters_hosts_and_reads_times(tmp_path: Path) -> None:
    created = datetime(2026, 9, 1, tzinfo=UTC)
    expires = datetime(2027, 9, 1, tzinfo=UTC)
    db = tmp_path / "Cookies"
    make_db(db, [
        (".instagram.com", "sessionid", "", encrypt_v10(b"SID-1"),
         webkit(created), webkit(expires)),
        (".instagram.com", "csrftoken", "plain-csrf", b"", webkit(created), 0),
        (".x.com", "auth_token", "", encrypt_v10(b"XTOK"), webkit(created), webkit(expires)),
        (".example.com", "sessionid", "", encrypt_v10(b"NOPE"), webkit(created), webkit(expires)),
    ])
    jar = ck.read_cookies(db, ["instagram", "x", "linkedin"])
    assert {(c.host_key, c.name) for c in jar} == {
        (".instagram.com", "sessionid"), (".instagram.com", "csrftoken"), (".x.com", "auth_token")}
    assert ck.instagram_jar(jar) == {"sessionid": "SID-1", "csrftoken": "plain-csrf"}
    info = ck.session_info(jar)
    assert info["instagram"] is not None
    assert info["instagram"].created_at == created and info["instagram"].expires_at == expires
    assert info["x"] is not None and info["linkedin"] is None
    assert ck.session_value(jar, "x") == "XTOK"


def test_the_copy_is_used_and_removed_never_the_live_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "Cookies"
    make_db(db, [(".instagram.com", "sessionid", "v", b"", 1, 0)])
    before = db.stat().st_mtime_ns
    seen: list[Path] = []
    real = ck.sqlite3.connect

    def spy(target: str, *a: object, **k: object):  # type: ignore[no-untyped-def]
        seen.append(Path(target.split("?")[0].removeprefix("file:")))
        return real(target, *a, **k)

    monkeypatch.setattr(ck.sqlite3, "connect", spy)
    ck.read_cookies(db, ["instagram"])
    assert seen and all(p != db for p in seen)
    assert not any(p.exists() for p in seen)
    assert db.stat().st_mtime_ns == before


def test_missing_or_corrupt_db_raises_cookie_error(tmp_path: Path) -> None:
    with pytest.raises(ck.CookieError):
        ck.read_cookies(tmp_path / "nope", ["instagram"])
    bad = tmp_path / "Cookies"
    bad.write_bytes(b"not a sqlite file at all" * 20)
    with pytest.raises(ck.CookieError):
        ck.read_cookies(bad, ["instagram"])


def test_one_undecryptable_row_fails_the_whole_read(tmp_path: Path) -> None:
    db = tmp_path / "Cookies"
    make_db(db, [(".instagram.com", "sessionid", "", b"v11" + b"\x00" * 16, 1, 0)])
    with pytest.raises(ck.CookieError):
        ck.read_cookies(db, ["instagram"])


def test_values_never_appear_in_repr_or_errors_and_fingerprint_is_8_hex() -> None:
    c = ck.ChromeCookie(".x.com", "auth_token", "SUPERSECRET", None, None)
    assert "SUPERSECRET" not in repr(c)
    assert c.fingerprint() == hashlib.sha256(b"SUPERSECRET").hexdigest()[:8]
    with pytest.raises(ck.CookieError) as ei:
        ck.decrypt_value(".x.com", b"v11SUPERSECRET-padding-pad")
    assert "SUPERSECRET" not in str(ei.value)


def test_newest_session_cookie_wins_across_hosts() -> None:
    old = ck.ChromeCookie(
        ".twitter.com", "auth_token", "OLD", datetime(2026, 1, 1, tzinfo=UTC), None)
    new = ck.ChromeCookie(".x.com", "auth_token", "NEW", datetime(2026, 6, 1, tzinfo=UTC), None)
    assert ck.session_value([old, new], "x") == "NEW"
    assert ck.session_value([new, old], "x") == "NEW"


def test_a_copy_with_a_hot_journal_reads_the_committed_rows(tmp_path: Path) -> None:
    """Copy db + journal while a transaction is mid-write (pages spilled to the
    db file, originals in the journal). The private copy is opened read-write,
    so SQLite rolls the hot journal back to the last commit."""
    live = tmp_path / "live"
    live.mkdir()
    db = live / "Cookies"
    created = webkit(datetime(2026, 9, 1, tzinfo=UTC))
    make_db(db, [(".instagram.com", "sessionid", f"committed-{i}", b"", created, 0)
                 for i in range(200)])
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA journal_mode=DELETE")
    writer.execute("PRAGMA cache_size=1")
    writer.execute("BEGIN")
    writer.execute("UPDATE cookies SET value = 'UNCOMMITTED-' || value || hex(randomblob(400))")
    journal = live / "Cookies-journal"
    assert journal.exists() and journal.stat().st_size > 0, "could not build a hot journal"
    snap = tmp_path / "snap"
    snap.mkdir()
    shutil.copy2(db, snap / "Cookies")
    shutil.copy2(journal, snap / "Cookies-journal")
    writer.execute("ROLLBACK")
    writer.close()

    jar = ck.read_cookies(snap / "Cookies", ["instagram"])
    assert len(jar) == 200
    assert all(c.value.startswith("committed-") for c in jar)
