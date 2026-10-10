---
type: design
title: "Shared files: Nora reads the files people share with her, found and known only inside the unit"
description: "Not built. Two vendor-neutral, read-only tools (shared_files_search, shared_file_read) over a SharedFilesSource port, with Google Drive as the first adapter, signed in as Nora's own Google account nora@67.zone. Discovery and knowledge are scoped to the unit (DEC-214): a unit's rooms find only files its members shared, and what she learns in one of its rooms is known in all of them and in no other unit. The credential stays in Nora's container, never in her computer."
tags: [nora, tools, shared-files, google-drive, units, identity, read-only]
timestamp: '2026-10-10'
status: proposed
---

# Shared files

Status (2026-10-10): **proposed, nothing built.**

## Operator direction (2026-10-10)

- Nora gets read-only access to files people share with her. She has her own
  Google account, `nora@67.zone` (a Google account on an existing address, not
  Workspace), so a person shares a file the usual way: Share, type
  `nora@67.zone`, Viewer.
- **Expose it as tools, vendor-neutral.** Google Drive is the first source,
  not the shape: no tool name, description or result says Google.
- **Visibility is unit-wise.** A file does not belong to a unit. The UNIT is
  the boundary of discovery and of knowledge: never the whole workspace, and
  never a single room. Sharing a file and talking about it on Signal must not
  leave Nora blind to it on the same unit's WhatsApp or Discord.

## 1. What Nora gets

| tool | does | returns |
|---|---|---|
| `shared_files_search(query?)` | lists files shared with her that this unit may see, newest first, matching `query` on name and full text when given | per file: a short code, name, kind, who shared it, modified time |
| `shared_file_read(code_or_link, part?, to="reply")` | reads one file as text; `to="computer"` copies it into her computer's `inbox/` instead | the text of `part` (default the first), the number of parts, kind |

Both are read-only and say so in their descriptions. Statuses follow the
house set: `ok`, `nothing_found`, `ambiguous`, `refused`, `unavailable`, each
with a `note`. Both are built with the `nora-tool` skill under ADR 0015,
including the `tool_selection` routing measurement before and after.

**Kinds and what a read returns.** Every source maps its files onto one
vocabulary, so the model never learns a vendor's MIME types:

| kind | read as |
|---|---|
| `document` | Markdown (headings, lists, tables kept; images dropped) |
| `spreadsheet` | every tab, each as a table with its tab name; a part is a tab or a row range |
| `presentation` | text per slide, numbered |
| `pdf` | text per page range |
| `other` | not read as text; `to="computer"` still copies it |

A part caps the text one call returns, so a long file is paged, never
truncated silently: the result says which part it is and how many there are.

## 2. The port

```
nora/infra/shared_files/
  port.py          SharedFilesSource: list_visible(), search(query), read(ref, part), fetch_bytes(ref)
  model.py         SharedFile{ref, name, kind, owners, shared_by, modified}
                   kind = document | spreadsheet | presentation | pdf | other
  google_drive.py  the first adapter
  fake.py          for tests
```

`ref` is opaque to everything above the adapter (source plus the provider's
file id). The model sees only a short code derived from it, never a provider
id (DEC-24). `shared_by` and `owners` are email addresses; the tool layer
resolves them to persons (§4) and the model sees names.

Config: `[shared_files]` is OFF by default, `sources = ["google_drive"]`, and
`[shared_files.google_drive]` names the environment variables that hold the
client id, client secret and refresh token (DEC-8: the values never sit in
config). While unwired, both tools answer `unavailable`, never an empty list
that reads as "nothing was shared".

A second source (OneDrive, Dropbox) is a new adapter and a new entry in
`sources`; the tools do not change.

## 3. Unit scope

The turn's unit comes from the existing resolver (`_unit()` and
`_unit_rooms()` in `nora/agent/tools.py`); its members are the persons its
rooms list (`_units_from_policy` in `nora/analytics/composition.py`). A room
open to everyone lists nobody and so adds no member: membership is never
guessed.

**Discovery.** `shared_files_search` lists a file in unit U only when the
person who shared it with Nora resolves to a member of U. When the source
does not say who shared it (a file in a shared drive, or shared through a
group), the owners stand in: some owner must be a member of U. A file whose
sharer and owners all resolve to nobody is invisible in every unit. It is
not an error, and the search result never hints that hidden files exist.

**A pasted link.** A Drive link pasted in a room of U is readable there and
in U's other rooms, whoever shared the file with Nora: the person who posted
it brought it into the unit. The link is read through Nora's account, so it
still needs to be shared with her or open to anyone with the link; otherwise
it is `refused` with a note to share it with her.

**Knowledge.** Nothing new is built for this half. What a read returns goes
into her reply, the reply is recorded in that room, and recall over the
record and Honcho is already unit-scoped (DEC-214). So what she learned in the
unit's Signal room is recalled in its WhatsApp and Discord rooms and in no
other unit. This design adds **no cache of file text**: every read goes to
the source, so there is no second store whose scope could drift from the
unit's. If a cache is added later it is keyed by unit.

**Fail closed.** No resolvable unit (a turn whose room the policy places in
no unit) gives `refused`, never a workspace-wide answer. The same goes for an
unreadable member list, or for a sharer whose email maps to two persons (an
identity error, refused at startup if it can be).

## 4. Identity: a person's email

Discovery turns an email into a person, so `identity.toml` needs one more
account kind:

```toml
[[persons.miles.google]]
email = "miles@example.com"
```

Matched case-insensitively on the full address. A person may list several
(personal and work), like any other account kind.

`google` is an ACCOUNT kind, not a chat platform: nothing may treat it as a
room platform, open an adapter for it, or count it as a platform in a unit
(the one-room-per-platform rule of DEC-214 is about chat rooms). How
`nora/domain/identity.py`'s `_ID_KINDS` admits a non-chat kind without a room
consumer noticing is the first build question, and S1's tests pin it.

## 5. The Google Drive adapter

**Sign-in (operator, once).**
1. Create the Google account `nora@67.zone`.
2. In Google Cloud, enable the Drive API and the Sheets API, and create an
   OAuth client of type Desktop app.
3. Set the OAuth consent screen to **In production**. In Testing, a refresh
   token expires after 7 days and the tools go `unavailable` a week after
   setup. The unverified-app warning is expected for one account.
4. Run the one-time auth script S1 adds, sign in as `nora@67.zone`,
   approve, and put the printed refresh token in norty's `.env`.

**Scopes:** `drive.readonly` and `spreadsheets.readonly`, nothing else. Read-
only therefore holds twice: in the scope, and in the Viewer role people grant.

**Calls.**

| need | call |
|---|---|
| visible files | `files.list`, `q = sharedWithMe and trashed = false`, fields `id, name, mimeType, modifiedTime, owners(emailAddress), sharingUser(emailAddress)` |
| full-text search | the same with `fullText contains '<query>'` |
| Google Doc | `files.export` as `text/markdown` |
| Google Sheet | `spreadsheets.get` for the tab names, then `spreadsheets.values.batchGet` (a CSV export returns the first tab only) |
| Google Slides | `files.export` as `text/plain` |
| PDF, docx, xlsx, pptx | `files.get?alt=media`, parsed on norty |

`files.export` is capped at about 10 MB of output. A larger file is `refused`
for `to="reply"` and still copies with `to="computer"`.

Access tokens come from the refresh token through Google's token endpoint and
live in memory only. An `invalid_grant` (a revoked or expired token) is
`unavailable` with a reason the operator can read in the log, and it
increments a counter that can carry an alert.

## 6. Into her computer, without the credential

The token never enters the computer VM (`design/sandbox-runtime/00-design.md`
§1: no master credential in the VM). `to="computer"` fetches the bytes in
Nora's container and hands them to the computer through the same path
`computer_import` uses, into `inbox/`, under the same size limit and lease.
Her computer reaching the public web (DEC-289) changes nothing here: a Drive
file needs Nora's account, and that account stays outside.

## 7. Trust

File text is content written by other people: it comes back wrapped
`untrusted="true"` and is never instructions. Nothing in this design writes,
comments, shares onward or changes permissions; the scopes could not if it
tried.

## 8. Links

A pasted Drive, Docs, Sheets or Slides link is claimed in `LINK_OWNERS`
(`nora/agent/link_owners.py`), so `web_fetch` and `media_fetch` point Nora at
`shared_file_read` instead of fetching Google's sign-in page.

## 9. Observability

A `{tool, status}` counter for both tools, and on search the count of files
listed versus hidden by the unit filter (a number, never names), so a filter
that hides everything is visible as a rate, not as "nothing was shared".

## 10. Build sequence

| step | ships |
|---|---|
| S1 | the port, the fake, the `google` account kind with its tests, the auth script, `[shared_files]` config off by default |
| S2 | the Google adapter: list, search, read for every kind, paging, the 10 MB refusal, token refresh |
| S3 | the two tools with unit scope (§3), the link-owner claim (§8), counters (§9), `tool_selection` rows before and after |
| S4 | `to="computer"` (§6) |
| S5 | live: sign-in, `.env`, config on, image rebuild, and a read-back of one search and one read per kind in two rooms of one unit and one room of another |

S5's read-back is the acceptance test: the same file is found in both rooms
of its unit and is not found in the other unit.

## 11. Not in scope

Writing or commenting; Gmail and Calendar as Nora; push notifications of new
shares (the Drive changes feed); images inside documents and slide visuals
(a later step could render slides as images for the vision path); a second
vendor.
