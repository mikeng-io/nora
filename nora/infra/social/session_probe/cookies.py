"""Read Chrome's cookie jar for the session probe. Values never leave here.

Chrome's `Cookies` SQLite file is copied to a temp path and the COPY is
opened: Chrome holds it open and writes it live, and opening it in place
risks a lock or a torn read (and, on a writable open, corruption). The COPY is
opened read-write so a copied hot journal can roll back; only the copy is ever
written. If the copy is still torn, the query fails and that is a
`CookieError`, never a partial jar. The copy is deleted before this module returns.

Decryption is Chrome-on-Linux with no keyring (the norty case, headed Chrome
under a plain VNC session): a `v10` blob is AES-128-CBC, key =
PBKDF2-HMAC-SHA1("peanuts", "saltysalt", 1 iteration, 16 bytes), IV = 16
spaces, PKCS7 padded. Since Chrome 130 the plaintext is prefixed with
sha256(host_key); it is stripped when present. A `v11` blob (keyring-backed
key) is refused, not guessed at.

**No cookie value is ever logged or printed** — `ChromeCookie.value` is
excluded from `repr`, and the only things this module exposes for logging are
names, lengths, a sha256 prefix and times.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .decide import CookieInfo, Network

#: The one cookie whose presence means «logged in» on each network.
SESSION_COOKIE: dict[Network, str] = {
    "instagram": "sessionid", "x": "auth_token", "linkedin": "li_at",
}

#: host_key suffixes that belong to each network.
NETWORK_HOSTS: dict[Network, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "x": ("x.com", "twitter.com"),
    "linkedin": ("linkedin.com",),
}

_WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
_KEY = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, dklen=16)
_IV = b" " * 16


class CookieError(Exception):
    """The jar could not be read or a value could not be decrypted.

    The message never contains a cookie value.
    """


@dataclass(frozen=True)
class ChromeCookie:
    host_key: str
    name: str
    value: str = field(repr=False)
    created_at: datetime | None
    expires_at: datetime | None

    def fingerprint(self) -> str:
        return fingerprint(self.value)


def fingerprint(value: str) -> str:
    """sha256 prefix, 8 hex chars: enough to tell two cookies apart in a log."""
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def webkit_to_datetime(us: int) -> datetime | None:
    """Chrome timestamps are microseconds since 1601; 0 means «session»."""
    if us <= 0:
        return None
    try:
        return _WEBKIT_EPOCH + timedelta(microseconds=us)
    except OverflowError:
        return None


def decrypt_value(host_key: str, blob: bytes) -> str:
    if blob[:3] != b"v10":
        prefix = blob[:3].decode("ascii", "replace")
        raise CookieError(f"unsupported cookie encryption prefix {prefix!r}")
    body = blob[3:]
    if not body or len(body) % 16:
        raise CookieError("encrypted cookie has a bad length")
    try:
        dec = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).decryptor()
        padded = dec.update(body) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise CookieError("cookie decrypt failed (bad padding)") from exc
    digest = hashlib.sha256(host_key.encode()).digest()
    if plain[:32] == digest:
        plain = plain[32:]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CookieError("decrypted cookie is not utf-8") from exc


def _host_matches(host_key: str, suffixes: Iterable[str]) -> bool:
    host = host_key.lstrip(".")
    return any(host == s or host.endswith("." + s) for s in suffixes)


def read_cookies(db_path: Path, networks: Sequence[Network]) -> list[ChromeCookie]:
    """All cookies for the given networks' hosts, decrypted.

    Raises `CookieError` when the DB is missing, unreadable or a row will not
    decrypt: a partial jar would read as «that network is logged out».
    """
    suffixes = tuple(s for n in networks for s in NETWORK_HOSTS[n])
    if not db_path.is_file():
        raise CookieError(f"cookie db not found: {db_path}")
    tmpdir = Path(tempfile.mkdtemp(prefix="nora-probe-"))
    copy = tmpdir / "Cookies"
    try:
        try:
            shutil.copy2(db_path, copy)
            copy.chmod(0o600)
            journal = db_path.with_name(db_path.name + "-journal")
            # A hot journal copied beside the db lets SQLite roll back a
            # mid-write copy to its last commit, but only on a READ-WRITE open
            # (a read-only open cannot roll back: "attempt to write a readonly
            # database"). The copy is a throwaway in a private 0700 dir, so it
            # is opened read-write; the original is never opened.
            if journal.is_file():
                shutil.copy2(journal, tmpdir / "Cookies-journal")
                (tmpdir / "Cookies-journal").chmod(0o600)
            con = sqlite3.connect(str(copy))
        except (OSError, sqlite3.Error) as exc:
            raise CookieError(f"cannot copy or open cookie db: {type(exc).__name__}") from exc
        try:
            rows = con.execute(
                "SELECT host_key, name, value, encrypted_value, creation_utc, expires_utc "
                "FROM cookies"
            ).fetchall()
        except sqlite3.Error as exc:
            raise CookieError(f"cookie query failed: {type(exc).__name__}") from exc
        finally:
            con.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    out: list[ChromeCookie] = []
    for host_key, name, value, enc, created, expires in rows:
        if not _host_matches(host_key, suffixes):
            continue
        text = value if value else (decrypt_value(host_key, bytes(enc)) if enc else "")
        out.append(
            ChromeCookie(
                host_key, name, text, webkit_to_datetime(created), webkit_to_datetime(expires)
            )
        )
    return out


def _networks_of(host_key: str) -> list[Network]:
    return [n for n, suf in NETWORK_HOSTS.items() if _host_matches(host_key, suf)]


def session_info(cookies: Iterable[ChromeCookie]) -> dict[Network, CookieInfo | None]:
    """Per network: the newest session cookie's times, or None when absent
    (an empty value counts as absent)."""
    best: dict[Network, ChromeCookie] = {}
    for c in cookies:
        for net in _networks_of(c.host_key):
            if c.name != SESSION_COOKIE[net] or not c.value:
                continue
            cur = best.get(net)
            floor = datetime.min.replace(tzinfo=UTC)
            if cur is None or (c.created_at or floor) >= (cur.created_at or floor):
                best[net] = c
    return {
        n: (CookieInfo(best[n].created_at, best[n].expires_at) if n in best else None)
        for n in SESSION_COOKIE
    }


def session_value(cookies: Iterable[ChromeCookie], network: Network) -> str | None:
    """The session cookie's value, for hashing/merging only. Callers must not log it."""
    found = [
        c for c in cookies
        if c.name == SESSION_COOKIE[network] and c.value and network in _networks_of(c.host_key)
    ]
    if not found:
        return None
    floor = datetime.min.replace(tzinfo=UTC)
    return max(found, key=lambda c: c.created_at or floor).value


def instagram_jar(cookies: Iterable[ChromeCookie]) -> dict[str, str]:
    """name -> value for Chrome's instagram.com cookies (newest wins a name clash)."""
    floor = datetime.min.replace(tzinfo=UTC)
    chosen: dict[str, ChromeCookie] = {}
    for c in cookies:
        if "instagram" not in _networks_of(c.host_key) or not c.value:
            continue
        cur = chosen.get(c.name)
        if cur is None or (c.created_at or floor) >= (cur.created_at or floor):
            chosen[c.name] = c
    return {n: c.value for n, c in chosen.items()}
