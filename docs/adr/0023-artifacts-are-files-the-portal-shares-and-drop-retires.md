# 0023 · Artifacts are files the portal shares behind sign-in; Drop and every link shortener retire

- **Status:** proposed (2026-10-08, operator direction: "the drop service becomes Artifacts, internal
  artifact sharing, like a shared drive", and "remove the use of link shorteners from everywhere")
- **Date:** 2026-10-08
- **Extends:** [0020](0020-documents-are-json-specs-the-portal-renders.md) point 5, which retired
  Drop's document use and left its file half and its shortener for "a later change". This is that
  change, and it reverses 0020's "Drop's link shortener stays".
- **Index:** DEC-295 (part 1); part 2 takes its own row

## Context

Read from the tree and from norty on 2026-10-08:

- **Drop has two halves left** (DEC-254). Its file half publishes `computer_export(to="link")`
  through `POST`/`PATCH /api/artifacts` and serves `/drop/{slug}` on a PUBLIC origin with no
  sign-in (`deploy/drop/server.py`). Its shortener mints `/i/{slug}` for the `route` and
  `place_info` tools' `maps_url` (`nora/infra/shortener.py`, deleted by this change, and
  `nora/agent/tools.py`).
- **The file half has never been used by the computer.** `computer_exports` on norty holds 4 rows,
  all `chat`; no `link` row exists. Drop's own `artifacts` table holds 15 rows, the newest from
  2026-09-04, all from the retired Smith document path (0020).
- **#757 widened what may leave the computer** from ten types to any file but a web page. Through
  Drop, that made any file publicly linkable by anyone holding the URL, which is the opposite of
  what a room's files are: they belong to the room.
- **The portal already has the access rule a room's files need.** A document is readable by its
  creator and by every person a room names in policy, checked at read time so leaving a room
  revokes access (`nora/web/documents.py`, `nora/infra/sql/documents.py` `_visible`). It already
  streams bytes from Nora's blob store behind that rule (`/app/v1/documents/{id}/media/{sha}`, with
  `nosniff`, a sandbox CSP and `no-store`), and it already has a paged, searchable list UI.
- **Every exported byte is already in Nora's custody.** `chat` and `keep` exports are put in the
  blob store (RustFS `nora-media` in production) and recorded with their room, title, type, size
  and sha256 in the insert-only `computer_exports` table. The table's `link` constraint requires
  `artifact_id` and `url`, and does not forbid `blob_key`, so a link row can carry its bytes'
  key with no migration.
- **A shortened link hides its destination.** `maps_url` is a Google Maps URL; the short form
  adds a hop through a service Nora runs, a Valkey store, a bearer token and a public tunnel rule,
  to save characters in a chat that renders links anyway.

## Decision

1. **Artifacts is the portal's shared drive.** A new portal section, `/artifacts`, lists the files
   Nora sent out of her computer in the rooms that name the viewer: every `chat` and `link`
   export, newest first, with its title, type, size, room and time, and a download. `keep`
   exports are Nora's private record and are never listed. The access rule is the documents
   rule, evaluated at read time; a file the viewer may not see is the same answer as one that
   does not exist.
2. **`computer_export(to="link")` returns a portal URL.** The bytes go to the blob store like
   `chat` and `keep`; the row records an `artifact_id` Nora mints (a short random handle) and
   `url = <[documents] public_url>/artifacts/<artifact_id>`. Opening it needs sign-in.
   `artifact_id` keeps its meaning: export again with it to replace the file behind the same
   link, allowed only from the room whose first row made it (`for_artifact`, unchanged).
   Without a `public_url`, `link` is `unavailable`, as it is today without Drop.
3. **The download route serves bytes from the portal origin, so it is strict.** Raster images,
   audio and video may display inline; every other type, SVG and PDF included, is
   `Content-Disposition: attachment`. Every response carries `nosniff`, `CSP: sandbox` and
   `no-store`. A web page never reaches this route: #757 still refuses HTML at export.
4. **No link shortener anywhere.** `maps_url` is the full Google Maps URL. `LinkShortener`, the
   `[shortener]` config section, `NORA_DROP_TOKEN`, `deploy/shortener/` and Drop's `/api/links`
   and `/i/{slug}` go.
5. **Drop retires.** The `drop` and `valkey` services, `deploy/drop/`, `nora/infra/drop.py`, the
   `ArtifactIngest` port and its fake are deleted, with the tunnel rules that route to them
   (outside git, on the deploy host). RustFS stays: Nora's blob store uses it. Drop's
   `artifacts` table and its `drop-artifacts` bucket are left in place, unread; deleting 15 rows
   of retired Smith output is a separate, irreversible step nobody needs yet.

## Consequences

**Better**

- A room's files are visible to the room and nobody else, revoked when someone leaves it.
- One place to find what Nora made, instead of scrolling back through a chat for an attachment.
- Two services, one token, one Valkey store and two public routes fewer to run and harden.
- Links say where they go.

**Worse**

- **Every Drop and short link already sent dies.** Old `/drop/` and `/i/` URLs in chat history
  stop resolving. Measured exposure: 15 Drop artifacts (newest 2026-09-04) and whatever `maps_url`
  short links the route tools sent.
- **A link now needs an account.** Someone outside `identity.toml` cannot open a file Nora
  linked; a file meant for them goes as a `chat` attachment instead.
- **Full Maps URLs are long** in a chat bubble.
- **The portal origin now serves arbitrary files.** It is the origin holding the session cookie,
  so a mistake in the download route's disposition rule is worse here than on Drop's separate
  origin. Decision 3 is the mitigation and is enforced by tests.

## Rejected

- **Keep Drop, add sign-in to it.** Drop would need its own session, identity and room rule, a
  second copy of what the portal already enforces.
- **Signed, expiring public URLs.** Anyone forwarded the link could open it until expiry; the
  operator asked for internal sharing.
- **List `keep` exports too.** `keep` is defined as "stores it privately and sends nothing"; listing
  it would make it a share.
- **Drop the `artifacts` table now.** Irreversible, and nothing reads it.
