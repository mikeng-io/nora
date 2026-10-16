"""Sign Nora's own Google account in, once, and print its ONE refresh token.

    python3 scripts/google_auth.py

The installed-app loopback flow, standard library only: it starts a one-shot
listener on 127.0.0.1, prints the consent URL (and tries to open it), and when
Google redirects back it exchanges the code for tokens and prints the REFRESH
token for norty's `.env` (`GOOGLE_OAUTH_REFRESH_TOKEN`, or whatever
`[google] refresh_token_env` names).

- **One identity for every Google service:** it asks
  for the UNION of the scopes every service declares
  (`nora.infra.google.scopes.all_scopes`, `drive.readonly` alone today), with
  `access_type=offline` and `prompt=consent` (a refresh token every time).
  NOT `include_granted_scopes`: scopes granted to this client before are not
  folded in, so the token holds what Nora's services declare and nothing
  else. Adding a service is a row in that table and one re-run of this script
  with the same client.
- **A grant that could write, undeclared, is refused:**
  if Google grants a scope that can write and that no service declared
  (`scopes.undeclared_write_scopes`), the refresh token is NOT printed and the
  script exits 1. Nora refuses such a token at runtime too.
- The client id and secret are read from `GOOGLE_OAUTH_CLIENT_ID` /
  `GOOGLE_OAUTH_CLIENT_SECRET`, or asked for (the secret without echo).
- **Nothing is written to disk.** The token is printed once; put it in `.env`
  yourself. A browser on the machine running this is required (run it on the
  Mac, not on norty).
- Sign in as `nora@67.zone`. With the consent screen in Testing, the token
  dies after 7 days: set it to In production first.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import http.server
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nora.infra.google.scopes import all_scopes, undeclared_write_scopes

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint
SCOPES = all_scopes()


def consent_url(*, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """The consent URL: every service's scope at once, offline access,
    always a consent screen (so a refresh token is always issued), no earlier
    grants folded in, PKCE S256."""
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def granted_is_safe(granted: frozenset[str]) -> bool:
    """False, with the reason on stderr, when the grant holds a scope that
    could write and that no service declared."""
    extra = undeclared_write_scopes(granted)
    if extra:
        print(f"refused: Google granted scopes that can write and that no Nora service "
              f"declared: {list(extra)}. The refresh token is not printed. Revoke "
              "this app's access in the account, then run this again.", file=sys.stderr)
        return False
    return True


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _wait_for_code(server: http.server.HTTPServer, state: str) -> str:
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            got.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab and return to the terminal.")

        def log_message(self, *args: object) -> None:  # the query holds the code
            return

    server.RequestHandlerClass = Handler
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("state") != state:
        raise SystemExit("the redirect's state did not match; nothing was exchanged")
    if "error" in got:
        raise SystemExit(f"Google refused the sign-in: {got['error']}")
    return got["code"]


def main() -> int:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip() or input("Client id: ").strip()
    client_secret = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        or getpass.getpass("Client secret: ").strip()
    )
    if not client_id or not client_secret:
        print("a client id and secret are both needed", file=sys.stderr)
        return 2
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    url = consent_url(
        client_id=client_id, redirect_uri=redirect_uri, state=state, challenge=challenge,
    )
    print("Sign in as nora@67.zone and approve:\n  " + "\n  ".join(SCOPES) + "\n\n" + url + "\n")
    webbrowser.open(url)
    try:
        code = _wait_for_code(server, state)
    finally:
        server.server_close()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "client_secret": client_secret, "code_verifier": verifier,
    }).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 - fixed https URL
        tokens = json.loads(resp.read())
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("Google returned no refresh token; nothing to print", file=sys.stderr)
        return 1
    granted = frozenset(str(tokens.get("scope", "")).split())
    if not granted_is_safe(granted):
        return 1
    missing = sorted(set(SCOPES) - granted)
    if missing:
        print(f"warning: not granted, those services stay unavailable: {missing}",
              file=sys.stderr)
    print("Refresh token (put it in .env as GOOGLE_OAUTH_REFRESH_TOKEN; it is not saved):\n")
    print(refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
