---
class: operations
subject: signing Nora's own provider account in for read-only file access
owns:
  - "provider sign-in: the one-time grant that gives Nora's account read-only access to a file provider"
links:
  - {to: docs/design/computer/external-files.md, rel: uses}
---

# External files: provider sign-in

Nora has one OAuth client and one refresh token for her own Google account, shared by every Google
service she uses, never a client or an environment variable per service. The top-level `[google]`
section names the three environment variables; the values never sit in config. One component owns
the refresh-token grant and the in-memory access token; each service's adapter asks it for a token
for its own scope and never sees the client secret or the refresh token. `invalid_grant` is counted
there once for all services.

Each service declares its scope in one table (Drive: `drive.readonly`). A service whose scope is
missing from the grant is `unavailable` with an operator log naming the scope and the fix, and the
others keep working. Adding a service is a row in that table and one re-run of the sign-in script,
same client.

**Sign-in (operator, once, and again when a service is added).**
1. Create Nora's Google account.
2. In Google Cloud, enable the Drive API and the Sheets API, and create an OAuth client of type
   Desktop app.
3. Set the OAuth consent screen to **In production**. In Testing, a refresh token expires after 7
   days and the tool goes `unavailable` a week after setup. The unverified-app warning is expected
   for one account.
4. Run `uv run python scripts/google_auth.py` on a machine with a browser, sign in as Nora's account
   and approve. It asks for the union of every service's scope (`access_type=offline`,
   `prompt=consent`; no `include_granted_scopes`, so nothing granted to the client earlier is folded
   in) and prints one refresh token for the refresh-token variable in the host's `.env`; it writes
   nothing. A grant holding a scope that could write and that no service declared is refused: no token
   is printed.

**Read-only in the credential.** The refresh token is shared, the access tokens are not: each
service's token is minted with an explicit `scope=` for the scopes it declared, so the Drive
adapter's token carries `drive.readonly` alone whatever else the grant holds. At runtime a grant
holding an undeclared write scope makes every Google service `unavailable`, with an error log naming
the scope.

An API key (the Maps key) is a different mechanism and stays with its feature.
