"""Sign Nora's own Google account in, once, and print its refresh token
(design/external-files/00-design.md §4, "Sign-in (operator, once)").

    python3 scripts/external_files_auth.py

The installed-app loopback flow, standard library only: it starts a one-shot
listener on 127.0.0.1, prints the consent URL (and tries to open it), and when
Google redirects back it exchanges the code for tokens and prints the REFRESH
token for norty's `.env` (`GOOGLE_DRIVE_REFRESH_TOKEN`, or whatever
`[external_files.google_drive] refresh_token_env` names).

- **One scope, `drive.readonly`**, with `access_type=offline` and
  `prompt=consent` so Google issues a refresh token every time. Nothing this
  token can do writes, comments or shares.
- The client id and secret are read from `GOOGLE_DRIVE_CLIENT_ID` /
  `GOOGLE_DRIVE_CLIENT_SECRET`, or asked for (the secret without echo).
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

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint
SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def consent_url(*, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    """The consent URL: one read-only scope, offline access, always a consent
    screen (so a refresh token is always issued), PKCE S256."""
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


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
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "").strip() or input("Client id: ").strip()
    client_secret = (
        os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
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
    print("Sign in as nora@67.zone and approve read-only Drive access:\n\n" + url + "\n")
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
    if SCOPE not in str(tokens.get("scope", "")).split():
        print(f"warning: the granted scope is {tokens.get('scope')!r}", file=sys.stderr)
    print("Refresh token (put it in .env as GOOGLE_DRIVE_REFRESH_TOKEN; it is not saved):\n")
    print(refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
