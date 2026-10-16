"""Every Google service Nora uses, and the ONE scope each declares
(one OAuth client and one refresh token for all of them).

The sign-in script (`scripts/google_auth.py`) asks for the union of these, so
adding a service is a row here, one re-run of the script, the same client.
Each consumer imports its own constant; `tests/unit/test_google_credentials.py`
keeps the script and this table in step. Standard library only, so the script
needs nothing but this module.
"""

from __future__ import annotations

#: Shared files: read-only Drive. The Sheets reads
#: accept it, and it cannot write, comment or share.
DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

#: consumer -> the scope it needs. One row per Google service.
CONSUMER_SCOPES: dict[str, str] = {
    "external_files.google_drive": DRIVE_READONLY,
}


def all_scopes() -> tuple[str, ...]:
    """The union the one refresh token must be granted, sorted."""
    return tuple(sorted(set(CONSUMER_SCOPES.values())))


#: Scopes that only identify the account; they read nothing of the user's.
_IDENTITY_SCOPES = frozenset({
    "openid", "email", "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
})


def is_read_only(scope: str) -> bool:
    """A scope that cannot change anything: a `.readonly` scope or an identity
    one. Everything else is treated as able to write, fail-closed."""
    return scope.endswith(".readonly") or scope in _IDENTITY_SCOPES


def undeclared_write_scopes(granted: frozenset[str] | set[str]) -> tuple[str, ...]:
    """Scopes in `granted` that could write and that no consumer declared,
    sorted. A grant with any is refused whole: read-only
    must hold in the credential, not only in the code that uses it."""
    declared = set(CONSUMER_SCOPES.values())
    return tuple(sorted(s for s in granted if s not in declared and not is_read_only(s)))
