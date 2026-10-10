"""`scripts/external_files_auth.py` asks for read-only Drive and nothing else,
offline, with a consent screen every time (design/external-files §4)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from scripts.external_files_auth import SCOPE, consent_url


def test_the_consent_url_asks_for_drive_readonly_offline_and_nothing_else():
    url = consent_url(client_id="cid", redirect_uri="http://127.0.0.1:5000",
                      state="s", challenge="c")
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert SCOPE == "https://www.googleapis.com/auth/drive.readonly"
    assert query["scope"] == SCOPE
    assert (query["access_type"], query["prompt"]) == ("offline", "consent")
    assert (query["code_challenge_method"], query["redirect_uri"]) == (
        "S256", "http://127.0.0.1:5000")
