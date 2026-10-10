---
type: design
title: "External files: Nora reads files people share with her, through one adapter per provider"
description: "Not built. A vendor-neutral ExternalFilesSource port with Google Drive as the first adapter, signed in read-only as Nora's own Google account nora@67.zone, and model-facing read tools whose number is decided at build. No new scoping: a file is readable once it is shared with her, and what she says about it is recorded and recalled like any other turn. The credential stays in Nora's container, never in her computer."
tags: [nora, tools, external-files, google-drive, adapter, read-only]
timestamp: '2026-10-10'
status: proposed
---

# External files

Status (2026-10-10): **proposed, nothing built.**

## Operator direction (2026-10-10)

- Give Nora the capability to read external files. People share a file with
  her own Google account, `nora@67.zone` (a Google account on an existing
  address, not Workspace), the usual way: Share, type the address, Viewer.
- **An adapter per provider.** Google Drive is the first one onboarded;
  Dropbox or another may follow. Nothing above the adapter names a vendor.
- **Tools read the files.** How many tools is not decided; it is settled at
  build (§3).
- **No new scoping.** Transcripts and memory already decide where what she
  learned is recalled. This capability adds no unit, workspace or room rule
  of its own.

## 1. The port

```
nora/infra/external_files/
  port.py          ExternalFilesSource: search(query), read(ref, part), fetch_bytes(ref)
  model.py         ExternalFile{ref, name, kind, shared_by, modified}
                   kind = document | spreadsheet | presentation | pdf | other
  google_drive.py  the first adapter
  fake.py          for tests
```

`ref` is opaque above the adapter (source plus the provider's file id). The
model sees a short code, never a provider id: a Drive id is a key-shaped
string, which R-18 keeps out of replies.

Config: `[external_files]` is OFF by default, `sources = ["google_drive"]`,
and `[external_files.google_drive]` names the environment variables that hold
the client id, client secret and refresh token (DEC-8: the values never sit
in config). While unwired, the tools answer `unavailable`, never an empty
result that reads as "nothing was shared".

A second provider is a new adapter and a new entry in `sources`; the tools do
not change.

## 2. One vocabulary for every provider

Every adapter maps its files onto these kinds, so the model never sees a
vendor's MIME types:

| kind | read as |
|---|---|
| `document` | Markdown (headings, lists, tables kept; images dropped) |
| `spreadsheet` | every tab, each a table with its tab name |
| `presentation` | text per slide, numbered |
| `pdf` | text by page range |
| `other` | not read as text; it can still be copied to her computer (§5) |

A long file is paged, never truncated silently: a result says which part it
is and how many there are.

## 3. Tools

The starting proposal is two, built with the `nora-tool` skill (ADR 0015):

- a **search** that lists files shared with her (name, kind, who shared it,
  modified, a short code), optionally matching a query on name and full text;
- a **read** that takes a code or a pasted link and returns one part as text,
  or copies the file to her computer (§5).

Whether that is one tool, two or three is decided by the `tool_selection`
routing measurement before and after, not here. Whatever the count, the tools
are read-only and say so, use the house statuses (`ok`, `nothing_found`,
`ambiguous`, `refused`, `unavailable`, each with a `note`), and name no
vendor.

A pasted Drive, Docs, Sheets or Slides link is claimed in `LINK_OWNERS`
(`nora/agent/link_owners.py`), so `web_fetch` and `media_fetch` point Nora at
the read tool instead of fetching a sign-in page. The owner's `label` says
"external files", not Google.

A link to a file that is not shared with her (and not open to anyone with
the link) is `refused`, with a note to share it with her.

## 4. The Google Drive adapter

**Sign-in (operator, once).**
1. Create the Google account `nora@67.zone`.
2. In Google Cloud, enable the Drive API and the Sheets API, and create an
   OAuth client of type Desktop app.
3. Set the OAuth consent screen to **In production**. In Testing, a refresh
   token expires after 7 days and the tools go `unavailable` a week after
   setup. The unverified-app warning is expected for one account.
4. Run the one-time auth script the first build step adds, sign in as
   `nora@67.zone`, approve, and put the printed refresh token in norty's
   `.env`.

**Scope:** `drive.readonly`, nothing else (the Sheets reads accept it).
Read-only holds twice: in the scope, and in the Viewer role people grant.

**Calls.**

| need | call |
|---|---|
| files shared with her | `files.list`, `q = sharedWithMe and trashed = false`, fields `id, name, mimeType, modifiedTime, sharingUser(emailAddress, displayName)` |
| full-text search | the same with `fullText contains '<query>'`, `'` and `\` escaped |
| Google Doc | `files.export` as `text/markdown` |
| Google Sheet | `spreadsheets.get` for the tab names, then `spreadsheets.values.batchGet` (a CSV export returns the first tab only) |
| Google Slides | `files.export` as `text/plain` |
| PDF, docx, xlsx, pptx | `files.get?alt=media`, parsed on norty |

`files.export` is capped at about 10 MB of output; a larger file is `refused`
as text and can still be copied to her computer.

`sharedWithMe` lists files shared directly. Files inside a shared folder
(which inherit access) and files in a shared drive (which need
`supportsAllDrives` and `includeItemsFromAllDrives`) are not listed in the
first build; a pasted link to one still reads. Whether to list them is
measured in the adapter step.

Access tokens come from the refresh token through Google's token endpoint and
live in memory only. `invalid_grant` (revoked or expired) is `unavailable`
with a reason in the log and a counter that can carry an alert.

## 5. Into her computer, without the credential

The token never enters the computer VM (`design/sandbox-runtime/00-design.md`
§1: no master credential in the VM). Copying a file to her computer fetches
the bytes in Nora's container and hands them over the path `computer_import`
uses, into `inbox/`, under its size limit and lease. Her computer reaching
the public web (DEC-289) does not change this: a shared file needs Nora's
account, and the account stays outside.

## 6. Trust

File text is written by other people: the result carries `"untrusted": True`
like every other tool result of outside text, and it is never instructions.
Nothing here writes, comments, shares onward or changes permissions, and the
scope could not if it tried.

## 7. Observability

A `{tool, status}` counter for every tool, and `invalid_grant` counted on its
own so a dead token is an alert, not a week of `unavailable`.

## 8. Build sequence

| step | ships |
|---|---|
| S1 | the port, the fake, the auth script, `[external_files]` config off by default |
| S2 | the Google Drive adapter: list, search, read for every kind, paging, the 10 MB refusal, token refresh |
| S3 | the tools (count settled by the routing measurement), the link-owner claim, counters |
| S4 | copy to her computer (§5) |
| S5 | live: sign-in, `.env`, config on, image rebuild, and a read-back of one search and one read per kind |

## 9. Not in scope

Writing or commenting; Gmail and Calendar as Nora; notification of new shares
(the Drive changes feed); images inside documents and slide visuals; a
second provider.
