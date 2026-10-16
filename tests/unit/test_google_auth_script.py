"""`scripts/google_auth.py` asks for every Google service's scope at once and
nothing else, offline, with a consent screen every time, no earlier grants
folded in, and refuses a grant that could write undeclared."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from nora.infra.external_files.google_drive import DRIVE_SCOPE
from nora.infra.google.scopes import CONSUMER_SCOPES, all_scopes
from scripts.google_auth import SCOPES, consent_url, granted_is_safe


def test_the_consent_url_asks_for_the_union_of_every_service_scope():
    url = consent_url(client_id="cid", redirect_uri="http://127.0.0.1:5000",
                      state="s", challenge="c")
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert set(query["scope"].split()) == set(CONSUMER_SCOPES.values()) == set(SCOPES)
    assert (query["access_type"], query["prompt"]) == ("offline", "consent")
    # No earlier grants folded in: the token holds the declared scopes only.
    assert "include_granted_scopes" not in query
    assert (query["code_challenge_method"], query["redirect_uri"]) == (
        "S256", "http://127.0.0.1:5000")


def test_drive_declares_the_read_only_scope_and_it_is_in_the_registry():
    assert DRIVE_SCOPE == "https://www.googleapis.com/auth/drive.readonly"
    assert DRIVE_SCOPE in all_scopes()


def test_a_grant_that_could_write_undeclared_is_refused_and_the_token_not_printed(capsys):
    assert granted_is_safe(frozenset(SCOPES))
    assert not granted_is_safe(frozenset({*SCOPES, "https://www.googleapis.com/auth/drive"}))
    assert "auth/drive'" in capsys.readouterr().err
