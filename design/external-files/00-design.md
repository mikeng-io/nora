---
type: design
title: "External files: a shared link becomes a handle, and Nora opens it only when asked"
description: "S1 merged, S2 built (off). A file link sent to Nora (Google Drive first, Dropbox maybe later) is recorded as a handle, the link and nothing else, the way an attachment becomes [image a3f2c1]. Its content is not fetched, read or remembered until somebody asks her to open it. Opening goes through a vendor-neutral tool over one adapter per provider, signed in read-only as Nora's own account nora@67.zone: the text comes back into the turn, or the file is copied to her computer. Read-only means the source file is never changed; what she makes from it goes out through her own tools."
tags: [nora, tools, external-files, google-drive, adapter, handles, read-only]
timestamp: '2026-10-10'
status: proposed
---

# External files

Status (2026-10-10): **S1 merged** (links recorded and rendered as handles,
the port, the fake, the code lookup). **S2 built, off** (the Google Drive
adapter `nora/infra/external_files/google_drive.py`, the text readers beside
it, Nora's one Google identity `nora/infra/google/` with
`scripts/google_auth.py`, the composition root's wiring); S3 to S5
not built. Revised the same day: the first version had a search over every
file shared with her. The operator's model is a link handle opened on demand,
and this text is that model.

S2 as built, where it differs from §4: Drive answers a file nobody shared
with her and a file that does not exist with the same bare 404, so a 404 is
`refused` (share it, then ask again) and `nothing_found` (deleted) is a file
in the bin, read from a `trashed` field added to `files.get`. Slides are
paged as one text, not numbered per slide: `text/plain` export marks no slide
boundary, and numbering them needs the Slides API (a `.pptx` file IS numbered
per slide). Office files are read with the standard library; PDFs with
`pypdf`.

S2 after review (2026-10-11): every parse of fetched bytes runs in a child
process with no secrets in its environment, a 20 s wall-clock deadline (past
it the child is killed and the open is `refused`, `too_slow`) and a 1 GiB
address-space cap; nothing is parsed on the event loop. Office parts are
read in one streaming pass, at most 64 elements deep, and a ZIP member that
is not stored or deflated is refused before it is opened. A Sheet the Sheets
API will not answer (a 403/404 after Drive answered for it: its link's
resource key reaches Drive only) is read from Drive's xlsx export instead,
and an API not enabled in the Cloud project is `unavailable` with an operator
log, never "not shared". A Sheet's tabs share one text budget.

## Operator direction (2026-10-10)

- Like a Google Drive MCP server, but as Nora's own tool, not an MCP.
- **When a file is sent to her, only the link is ingested** into the record
  and memory. The content is irrelevant to her until somebody asks her to
  open it.
- **Opening needs a handle.** From the handle she reads the file, downloads it
  to her computer, or transforms it and puts the result into a document.
- **That is what read-only means**: she never changes the source file.
- An adapter per provider. Google Drive is the first onboarded; Dropbox or
  another may follow. Nothing above the adapter names a vendor.

## 1. The handle: a link, nothing more

When a message arrives on any platform, each link a provider adapter
recognises as one of its files (a Drive, Docs, Sheets or Slides URL for
Google) is recorded with the message as an external-file reference: the
provider, the provider's file id and the URL as posted. **No network call is
made at ingest.** No name, no size, no content: nothing is known about the
file except that someone sent this link.

The record keeps the message body verbatim, URL included (Postgres is the
record). What the MODEL sees is rendered: the URL is replaced, in place, by a
LINK handle, words naming what the URL says it points to and a short code:
`[Google Doc link c41d09]`, `[Google Sheet link c41d09]`, `[Google Slides
link c41d09]`, and `[Google Drive link c41d09]` for a
`drive.google.com/file|open|uc` link. It says «link», never «file»: Nora
holds no file, only the link (operator, 2026-10-10). The words come from the
URL alone, with no network call, from one table the provider's adapter owns,
so a second provider brings its own words. So the model never sees a
provider file id (a Drive id is a key-shaped string, which R-18 keeps out of
replies), and the link cannot be echoed. This differs from an attachment,
whose placeholder is appended after the text: here the handle stands where
the link was.

The code follows the media handle's shape (`HANDLE_CHARS` in
`nora/domain/media.py`): six hex characters, here of a hash of the provider
and file id rather than of content, legible and quotable back to her, with the
full reference stored beside the message. The same file posted twice is the
same code.

A folder link is not a file and is not recognised in the first build: it
stays an ordinary link.

A file link carried INSIDE another URL (Google's own `/url?q=` redirect,
Outlook SafeLinks, Facebook's `l.php?u=`, a `?next=`) is found by looking in
the wrapper's query and tail, percent-decoded and to a bounded depth, and the
WHOLE wrapper is rendered as the handle: a wrapper left standing would carry
the file id to the model. Two links glued together are two links.

Because the handle lives in the message, it is recorded, recalled and scoped
exactly like the message around it. This design adds no scoping rule and no
memory of its own: she remembers that a file was sent, and what anyone said
about it, never what is in it.

## 2. Opening: only when asked

Opening is a tool call with the handle (or the pasted link). Nothing opens a
file on its own: not ingest, not a background pass, not the Dreamer.

What an open can do, all of it read-only on the source:

| asked for | the tool does |
|---|---|
| read it here | returns one part of the file as text into the turn |
| work on it | copies the file to her computer's `inbox/` (§5) |
| make something from it | nothing new: she reads it, then makes the result with the tools she has (`document_publish` for a document, the computer for data) |

The text she reads is part of that turn only. It enters memory the way
anything else in a turn does: through what she says in reply. There is no
cache of file text and no index of it.

**Never cached (operator, 2026-10-10).** Nothing fetched through a link is
kept: not content, not text, not bytes, and not metadata either (name, kind,
owner and modified time can all change). A linked file is LIVE and changes
over time, unlike an attachment sent to the room, whose bytes are fixed the
moment they arrive. So ingest stores the parsed URL ref and nothing else;
every open fetches fresh from the provider; there is no blob-store copy, no
media-table row, no memoization and no in-process cache of reads. The handle's
code hashes `provider:file_id` only, never content, so it stays the same while
the file changes. `tests/architecture/test_external_files_are_never_cached.py`
holds the code to it.

**Kinds.** Every adapter maps its files onto one vocabulary, so the model
never sees a vendor's MIME types:

| kind | read as |
|---|---|
| `document` | Markdown (headings, lists, tables kept; images dropped) |
| `spreadsheet` | every tab, each a table with its tab name |
| `presentation` | text per slide, numbered |
| `pdf` | text by page range |
| `other` | not read as text; it can still be copied to her computer |

A long file is paged, never truncated silently: a result says which part it
is and how many there are. The first open also returns what ingest never
knew: the file's name, kind, owner and last-modified time.

## 3. The tool

The starting proposal is **one tool**, opening a handle or a link, with a
`part` and a destination (`reply` or `computer`). Whether it stays one or
splits (for example, read and copy apart) is decided by the `tool_selection`
routing measurement before and after, built with the `nora-tool` skill (ADR
0015), not here. Whatever the count, the tools are read-only and say so, use
the house statuses (`ok`, `nothing_found`, `ambiguous`, `refused`,
`unavailable`, each with a `note`), and name no vendor.

Statuses specific to a file:

- `refused`, file not shared with her: the note says to share it with
  `nora@67.zone` as Viewer, then ask again. A link open to anyone reads
  without a share.
- `refused`, too large to read as text: it can still be copied.
- `nothing_found`: the code is not in any message she can see, or the file
  was deleted.

A pasted provider link is claimed in `LINK_OWNERS` (`nora/agent/link_owners.py`),
so `web_fetch` and `media_fetch` point her at this tool instead of fetching a
sign-in page. The owner's `label` says "external files", not Google.

The tool also accepts a link, for one that reached her some other way (a
fetched page, a document); the result never echoes it.

## 4. The port and the Google Drive adapter

```
nora/infra/external_files/
  port.py          ExternalFilesSource:
                     recognise(url) -> ref | None     pure, no network (ingest, §1)
                     describe(ref)  -> ExternalFile   name, kind, owner, modified
                     read(ref, part) -> text part
                     fetch_bytes(ref) -> bytes        for the computer copy
  google_drive.py  the first adapter
  fake.py          for tests
```

Config: `[external_files]` is OFF by default, `sources = ["google_drive"]`.
With it off, links are still recorded as handles (recognising a URL needs no
account), and the tool answers `unavailable`. A second provider is a new
adapter and a new entry in `sources`.

**One Google identity (operator, 2026-10-10).** Nora has ONE Google OAuth
client and ONE refresh token for her account, shared by every Google service
she will use (Drive now; Calendar, Gmail and the rest later), never a client
or an environment variable per service. The top-level `[google]` section
names the three environment variables (DEC-8: `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`; the values never
sit in config). One component, `nora/infra/google/auth.py`
(`GoogleCredentials`), owns the refresh-token grant and the in-memory access
token; each service's adapter is handed it and asks for a token for its own
scope, and never sees the client secret or the refresh token.
`invalid_grant` is counted there once for all of them
(`nora_google_invalid_grant_total`).

Each service DECLARES its scope in one table, `nora/infra/google/scopes.py`
(Drive: `drive.readonly`). The token endpoint's `scope` field is what the
refresh token was granted: a service whose scope is missing is `unavailable`
with an operator log naming the scope and the fix, and the others keep
working (fail closed per service). Adding a service is a row in that table
and one re-run of the sign-in script, same client.

`[google]` is the OAuth identity only. An API KEY (the Maps key,
`[route] api_key_env`) is a different mechanism and stays with its feature.

**Sign-in (operator, once, and again when a service is added).**
1. Create the Google account `nora@67.zone`.
2. In Google Cloud, enable the Drive API and the Sheets API, and create an
   OAuth client of type Desktop app.
3. Set the OAuth consent screen to **In production**. In Testing, a refresh
   token expires after 7 days and the tool goes `unavailable` a week after
   setup. The unverified-app warning is expected for one account.
4. Run `uv run python scripts/google_auth.py` on a machine with a browser,
   sign in as `nora@67.zone` and approve. It asks for the union of every
   service's scope (`access_type=offline`, `prompt=consent`,
   `include_granted_scopes=true`) and prints one refresh token for
   `GOOGLE_OAUTH_REFRESH_TOKEN` in norty's `.env`; it writes nothing.

**Scope:** `drive.readonly`, nothing else (the Sheets reads accept it). It
cannot write, comment or share; neither can the Viewer role people grant.

**Calls, all at open time.**

| need | call |
|---|---|
| describe | `files.get`, fields `id, name, mimeType, modifiedTime, size, owners(displayName)`; `size` is absent for native Google files |
| Google Doc | `files.export` as `text/markdown` |
| Google Sheet | `spreadsheets.get` for the tab names, then `spreadsheets.values.batchGet` (a CSV export returns the first tab only) |
| Google Slides | `files.export` as `text/plain` |
| PDF, docx, xlsx, pptx | `files.get?alt=media`, parsed on norty |

A file in a shared folder or a shared drive opens by its link like any other
(the folder's or drive's share grants access); every call sets
`supportsAllDrives` so shared-drive files resolve. An older "anyone with the
link" file may need its resource key: `recognise` keeps the `resourcekey`
from the URL and the adapter sends it in `X-Goog-Drive-Resource-Keys`. `files.export` is capped at about 10 MB of output.

Access tokens come from the refresh token and live in memory only.
`invalid_grant` (revoked or expired) is `unavailable` with a reason in the
log.

## 5. Into her computer, without the credential

The token never enters the computer VM (`design/sandbox-runtime/00-design.md`
§1: no master credential in the VM). A copy fetches the bytes in Nora's
container and writes them into `inbox/` under the same size limit
(`import_max_bytes`) and lease `computer_import` uses. That copy is a
point-in-time SNAPSHOT, not a cache: the result labels it with the file's
modified time as the provider reported it at that open, so she can say how
old her copy is, and a later open fetches afresh. That logic is inline in
`computer_import` today, so S4 first moves it into a shared function both
tools call. Her computer reaching the public web
(DEC-289) does not change this: a shared file needs Nora's account, and the
account stays outside.

## 6. Trust

File text is written by other people: it goes into one labelled untrusted
block, escaped and clamped (R-13, the `wrap_outside_text` shape the social
reads use in `nora/infra/social/render.py`), and it is never instructions.
Name, kind and modified time travel as typed fields outside the block.

## 7. Observability

A `{tool, status}` counter, `invalid_grant` counted on its own so a dead token
is an alert rather than a week of `unavailable`, and a count of handles
recorded at ingest per provider, so "links arrive but nobody opens them" is a
number.

## 8. Build sequence

| step | ships |
|---|---|
| S1 | the port and fake; `recognise` for Google URLs; a new ingest stage that records each recognised link beside its message (new storage: no link extraction exists today, and `media_json` is keyed by content hash); the render that replaces the URL with a link handle such as `[Google Doc link c41d09]` (the labels in the provider's own table, read back by `link_handles_in`); the code lookup; config off by default |
| S2 | the Google Drive adapter: describe, read for every kind, paging, the 10 MB refusal, token refresh; the auth script |
| S3 | the open tool (count settled by the routing measurement), the link-owner claim, counters |
| S4 | the copy to her computer (§5) |
| S5 | live: sign-in, `.env`, config on, image rebuild; read back a link recorded as a handle with no API call, then one open per kind |

S1 is useful alone: links become handles in the record before any account
exists.

## 9. Not in scope

Writing to, commenting on or sharing the source file; a search or listing of
everything shared with her (her way in is a link someone sent); fetching
anything at ingest; Gmail and Calendar as Nora; images inside documents and
slide visuals; a second provider.
