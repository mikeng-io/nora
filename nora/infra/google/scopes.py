"""Every Google service Nora uses, and the ONE scope each declares
(operator, 2026-10-10: one OAuth client and one refresh token for all of them).

The sign-in script (`scripts/google_auth.py`) asks for the union of these, so
adding a service is a row here, one re-run of the script, the same client.
Each consumer imports its own constant; `tests/unit/test_google_credentials.py`
keeps the script and this table in step. Standard library only, so the script
needs nothing but this module.
"""

from __future__ import annotations

#: Shared files (design/external-files §4): read-only Drive. The Sheets reads
#: accept it, and it cannot write, comment or share.
DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

#: consumer -> the scope it needs. One row per Google service.
CONSUMER_SCOPES: dict[str, str] = {
    "external_files.google_drive": DRIVE_READONLY,
}


def all_scopes() -> tuple[str, ...]:
    """The union the one refresh token must be granted, sorted."""
    return tuple(sorted(set(CONSUMER_SCOPES.values())))
