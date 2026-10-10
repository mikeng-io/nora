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
id: a Drive id is a key-shaped string, which R-18 keeps out of replies. `shared_by` and `owners` are email addresses; the tool layer
resolves them to persons (§4) and the model sees names.

Config: `[shared_files]` is OFF by default, `sources = ["google_drive"]`, and
`[shared_files.google_drive]` names the environment variables that hold the
client id, client secret and refresh token (DEC-8: the values never sit in
config). While unwired, both tools answer `unavailable`, never an empty list
that reads as "nothing was shared".

A second source (OneDrive, Dropbox) is a new adapter and a new entry in
`sources`; the tools do not change.

## 3. Unit scope

The turn's unit name comes from `_unit()` in `nora/agent/tools.py` (it honours
a Dreamer run's supplied unit; `_unit_rooms()` does not). Its members are the
persons its rooms list, the rule `_units_from_policy` in
`nora/analytics/composition.py` already applies: a room open to everyone lists
nobody and so adds no member, and membership is never guessed. **That
function is not reusable as is**: it runs only in the analytics process, and
the agent's `UnitRooms` (`nora/domain/self_presence.py`) carries no members. A
per-unit member set built once from policy at startup, beside the room
directory, is new wiring in S3.

This scoping assumes `policy.toml` declares `[units]` (it does on norty).
Without it every room falls in the default unit and the boundary is the whole
deployment, so the tools refuse to start when `[units]` is absent.

**Discovery.** `shared_files_search` lists a file in unit U only when the
person who shared it with Nora resolves to a member of U. When the source
does not name a sharer, an owner who is a member of U stands in. A file whose
sharer and owners all resolve to nobody is invisible in every unit. It is
not an error, and the search result never hints that hidden files exist.

**A person in several units** is the live case, not an edge: on norty one
person is listed in the rooms of three units. Under this rule, a file that
person shares is discoverable in all three. Whether that is wanted is an open
operator ruling (§12); the rest of the design does not depend on the answer.

**A code is not a pass.** Codes are deterministic, so a person can repeat one
in another unit. `shared_file_read` given a code applies the discovery rule
again for the turn's unit and answers `nothing_found` when it fails, exactly
as if the code did not exist.

**A pasted link.** A Drive link that a PERSON posted in a room of U is
readable in U's rooms, whoever shared the file with Nora: the person who
posted it brought it into the unit. The tool does not take the model's word
for that. It looks the link up in the record of U's rooms (messages from
persons, not Nora's own replies, not tool results, not fetched pages), and a
link it cannot find there falls back to the discovery rule. Without that
check, a link guessed or planted in untrusted text would read a file from
another unit. The read goes through Nora's account, so the file must still be
shared with her or open to anyone with the link; otherwise it is `refused`
with a note to share it with her.

**Knowledge.** What carries a file across the unit is discovery, not memory:
in any of U's rooms she can find and re-read what U may see. Her memory does
not do it alone, and the design does not pretend it does:

- **Room-scoped today:** the recent transcript in her prompt,
  `load_transcript`, `search_memory` (one room's Honcho session) and Standing.
  What she read in the Signal room is not in the WhatsApp room's transcript.
- **Unit-scoped today:** unit-keyed notes, Honcho `chat` and representation
  reads (`scope=<unit>`), and an explicit `room_read` of a sibling room.
- **Wider than the unit:** `about` reads Honcho's peer card, which takes no
  scope and spans the Honcho workspace (DEC-214 says so). A conclusion about
  a PERSON drawn from a file can surface in another unit of the same
  workspace. This is the existing rule for every conclusion about a person,
  not new with files, and this design does not change it.

This design adds **no cache of file text**: every read goes to the source, so
there is no second store whose scope could drift from the unit's. If a cache
is added later it is keyed by unit.

**Fail closed.** No resolvable unit (a room missing from policy; a room in
policy without an explicit unit lands in the default unit `nora`) gives
`refused`, never a workspace-wide answer. The same goes for an
unreadable member list, or for a sharer whose email maps to two persons (an
identity error, refused at startup if it can be).

## 4. Identity: a person's email

Discovery turns an email into a person, so `identity.toml` needs one more
account kind:

```toml
[[persons.miles.google]]
email = "miles@example.com"
```

Matched case-insensitively on the full address, casefolded BEFORE the
duplicate check in `nora/persons.py` (otherwise `A@x` and `a@x` pass as two
accounts). A person may list several (personal and work), like any other
account kind.

`google` is an ACCOUNT kind, not a chat platform: nothing may treat it as a
room platform, open an adapter for it, or count it as a platform in a unit
(the one-room-per-platform rule of DEC-214 is about chat rooms).
`nora/domain/identity.py`'s `_ID_KINDS` already admits non-chat kinds
(`life360`, `pulse`); `google` follows them, and S1's tests pin that no room
consumer sees it.

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

**Scope:** `drive.readonly`, nothing else (the Sheets reads below accept it).
Read-only therefore holds twice: in the scope, and in the Viewer role people
grant.

**Calls.**

| need | call |
|---|---|
| visible files | `files.list`, `q = sharedWithMe and trashed = false`, fields `id, name, mimeType, modifiedTime, owners(emailAddress), sharingUser(emailAddress)` |
| full-text search | the same with `fullText contains '<query>'`, `'` and `\` escaped |
| Google Doc | `files.export` as `text/markdown` |
| Google Sheet | `spreadsheets.get` for the tab names, then `spreadsheets.values.batchGet` (a CSV export returns the first tab only) |
| Google Slides | `files.export` as `text/plain` |
| PDF, docx, xlsx, pptx | `files.get?alt=media`, parsed on norty |

`sharedWithMe` matches files shared directly. Two cases it does not cover,
both failing closed (invisible, never wrongly visible), and left for later:
files inside a shared FOLDER (they inherit access and are not listed; S2
measures whether listing the folder's children is worth adding), and files in
a SHARED DRIVE (listed only with `supportsAllDrives` and
`includeItemsFromAllDrives`, and their `owners` field is empty, so only
`sharingUser` could place them). `sharingUser` names one person: a file shared
with Nora by two people reports one of them.

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

**The computer is room-scoped, and that is an exception to the unit rule.**
Its workspace key is one room (`computer_scope` in `nora/domain/computer.py`),
so a file copied in the Signal room is not in the WhatsApp room's workspace.
This design keeps that: the file itself stays discoverable unit-wide, so the
other room copies it again in one call. Making the computer unit-scoped is a
change to the computer, not to this design.

## 7. Trust

File text is content written by other people: the result carries
`"untrusted": True` like every other tool result of outside text, and it is
never instructions. Nothing in this design writes,
comments, shares onward or changes permissions; the scopes could not if it
tried.

## 8. Links

A pasted Drive, Docs, Sheets or Slides link is claimed in `LINK_OWNERS`
(`nora/agent/link_owners.py`), so `web_fetch` and `media_fetch` point Nora at
`shared_file_read` instead of fetching Google's sign-in page. The redirect
note shows the owner's `label`, so the label says "shared files", not Google.

## 9. Observability

A `{tool, status}` counter for both tools, and on search the count of files
listed versus hidden by the unit filter (a number, never names), so a filter
that hides everything is visible as a rate, not as "nothing was shared".

## 10. Build sequence

| step | ships |
|---|---|
| S1 | the port, the fake, the `google` account kind with its tests, the auth script, `[shared_files]` config off by default |
| S2 | the Google adapter: list, search, read for every kind, paging, the 10 MB refusal, token refresh |
| S3 | the per-unit member set, the two tools with unit scope (§3: discovery, the code re-check, the pasted-link record lookup), the link-owner claim (§8), counters (§9), `tool_selection` rows before and after |
| S4 | `to="computer"` (§6) |
| S5 | live: sign-in, `.env`, config on, image rebuild, and a read-back of one search and one read per kind in two rooms of one unit and one room of another |

S5's read-back is the acceptance test: the same file is found in both rooms
of its unit and is not found in the other unit.

## 11. Not in scope

Writing or commenting; Gmail and Calendar as Nora; push notifications of new
shares (the Drive changes feed); images inside documents and slide visuals
(a later step could render slides as images for the vision path); a second
vendor.

## 12. Open for the operator

1. **A person in several units.** A file that person shares is
   discoverable in every unit they belong to (§3). Keep that, or narrow
   discovery for multi-unit sharers?
