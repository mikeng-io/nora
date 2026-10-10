---
type: design
title: "External files: a shared link becomes a handle, and Nora opens it only when asked"
description: "S1 merged; S2, S3 and S4 built (off). A file link sent to Nora (Google Drive first, Dropbox maybe later) is recorded as a handle, the link and nothing else, the way an attachment becomes [image a3f2c1]. Its content is not fetched, read or remembered until somebody asks her to open it. Opening goes through a vendor-neutral tool over one adapter per provider, signed in read-only as Nora's own account nora@67.zone: the text comes back into the turn, or the file is copied to her computer. Read-only means the source file is never changed; what she makes from it goes out through her own tools."
tags: [nora, tools, external-files, google-drive, adapter, handles, read-only]
timestamp: '2026-10-11'
status: proposed
---

# External files

Status (2026-10-10): **S1 merged** (links recorded and rendered as handles,
the port, the fake, the code lookup). **S2 built, off** (the Google Drive
adapter `nora/infra/external_files/google_drive.py`, the text readers beside
it, Nora's one Google identity `nora/infra/google/` with
`scripts/google_auth.py`, the composition root's wiring). **S3
built**: one tool, `file_fetch(code | link, part)` (named with the existing
`fetch` verb, ADR 0015 §1), the link-owner claim (made only while a source
is wired), `nora_tool_calls_total` for it; its `tool_selection` rows are
written and NOT yet run. **S4 built** (2026-10-11): the copy is
`computer_import`'s, which now also takes a link handle's code or a file
link; `file_fetch` stays the read (§3, §5 as built).
S5 not built. Revised the same day: the
first version had a search over every
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
| work on it | `computer_import` copies the file to her computer's `inbox/` (§5) |
| see a picture or a video | `media_fetch` looks at the file's bytes, never kept (below) |
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

**`media_fetch` is a byte consumer too (2026-10-11).** It asks one neutral
question, «the bytes for this code or link», answered in order: a chat
attachment's code (stored media); a shared file's link handle code in this
room, a pasted file link, or a link she built around a code
(`ExternalFilesSource.fetch_bytes`, whatever provider is wired); any other
public link (the download). A bare code a sent picture and a link handle
share, or two link handles share, is `ambiguous`; a labelled handle
(`[Google Drive link c41d09]`) or a link built around a code already names a
file and is looked up among file links only. A pasted link the domain's
recogniser knows never reaches the public download: unwired, or a provider
the wired source cannot open, is `unavailable`. `media_fetch` asks
`fetch_bytes` with `accept=("image/", "video/")`, so the adapter refuses any
other file (`refused`, `NOT_ACCEPTED`, with its kind) from its one metadata
call, before any export or download; the answer is `not_an_image`, naming
`file_fetch` for a document, spreadsheet, presentation or PDF, and otherwise
saying it cannot be looked at or read here (and `computer_import`, when her
computer is on). The tool names no provider, host or URL rule. Its bytes and
its reading are never kept: no stored reading, no vision cache, no video
reading cache, no custody row, so two looks are two fetches
(`tests/unit/test_media_fetch_shared_file.py`; the static check above cannot
see inside `media_fetch`, which holds the stores for attachments). The answer
names the handle only.

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
`part` and a destination (`reply` or `computer`). **Split at S4 (2026-10-11, review of PR 807):** `file_fetch` is the read
only, with no destination, and stays declared read-only (the Dreamer ceiling,
R-7's wording, R-97's lookup set, the gate's capability digest all key on
that). The copy is `computer_import`'s, the declared write that already puts
a sent file into `inbox/` (§5). Whatever the count, the reading tools are read-only and say so, use
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
so `web_fetch` points her at this tool instead of fetching a sign-in page;
`computer_import` and `media_fetch` accept the link (`LinkInput.accepts`) and
take the file's bytes through the same source. A picture or a video handed to
this tool is refused as text, and the note names `media_fetch`. The owner's
`label` says "external files", not Google.

The tool also accepts a link, for one that reached her some other way (a
fetched page, a document); the result never echoes it.

## 4. The port and the Google Drive adapter

```
nora/infra/external_files/
  port.py          ExternalFilesSource:
                     recognise(url) -> ref | None     pure, no network (ingest, §1)
                     describe(ref)  -> ExternalFile   name, kind, owner, modified
                     read(ref, part) -> text part
                     fetch_bytes(ref) -> FileBytes    bytes + media type, for the computer copy
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
   service's scope (`access_type=offline`, `prompt=consent`; no
   `include_granted_scopes`, so nothing granted to the client earlier is
   folded in) and prints one refresh token for `GOOGLE_OAUTH_REFRESH_TOKEN`
   in norty's `.env`; it writes nothing. A grant holding a scope that could
   write and that no service declared is refused: no token is printed.

**Read-only in the credential** (re-review, 2026-10-11). The refresh token
is shared, the access tokens are not: each service's token is minted with an
explicit `scope=` for the scopes it declared, so the Drive adapter's token
carries `drive.readonly` alone whatever else the grant holds. At runtime a
grant holding an undeclared write scope makes every Google service
`unavailable`, with an error log naming the scope.

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
old her copy is, and a later open fetches afresh. Her computer reaching the
public web (DEC-289) does not change this: a shared file needs Nora's
account, and the account stays outside.

**As built (S4, 2026-10-11).** The copy is `computer_import`'s:
`computer_import(code | link)` takes, besides a media code, a link handle's
code (`[Google Sheet link c41d09]`, resolved among this room's own rows like
`file_fetch`'s) or a file link (a link pasted into `code` counts as one).
A code that names both a sent file and a link handle, or two link handles,
is `ambiguous` with a note asking which: never a guess. The bytes come from
`ExternalFilesSource.fetch_bytes`, which answers `FileBytes(data,
media_type)`: a Doc, Sheet or Slides deck exported as docx, xlsx or pptx, a
stored file as stored, every byte under the adapter's 10 MB cap. The fetch
runs after the computer-off check and BEFORE the room's lease, so a slow
provider never holds her computer busy; then the write path every import
shares (`_computer_copy_in` in `nora/agent/tools.py`): the lease, the
`started` and `finished` audit rows, `import_max_bytes`, and the path
`inbox/<12 hex of the bytes' sha256><suffix>`, built by Nora's code. The
suffix is the media type's; the owner's file name is read only for
`application/octet-stream`, so a PDF named `invoice.sh` lands as `.pdf`. The
answer is `status`, `note` (the copy is a snapshot), `name`, `kind`, `path`,
`size` and `modified`. The answer, the audit rows and the log name the
handle, never the file id or the URL: the adapter strips the id from the
Drive and Sheets URLs `httpx` logs. Nothing keeps the bytes past the call:
two copies are two fetches, and an edited file lands under a new name.

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
| S4 | the copy to her computer (§5): `computer_import` takes a link handle's code or a file link; built 2026-10-11, its `tool_selection` row not yet run |
| S5 | live: sign-in, `.env`, config on, image rebuild; read back a link recorded as a handle with no API call, then one open per kind |

S1 is useful alone: links become handles in the record before any account
exists.

## 9. Not in scope

Writing to, commenting on or sharing the source file; a search or listing of
everything shared with her (her way in is a link someone sent); fetching
anything at ingest; Gmail and Calendar as Nora; images inside documents and
slide visuals; a second provider.
