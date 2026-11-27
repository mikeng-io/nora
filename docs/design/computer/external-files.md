---
class: design
subject: a shared provider file is a handle, opened only on request and never kept
owns:
  - link handle: "the short code that stands in a message for a file link and holds nothing of the file"
  - file adapter: "the one component per provider that recognises its links and reads its files"
area: computer
links:
  - {to: docs/design/computer/README.md, rel: uses}
  - {to: docs/design/computer/sandbox.md, rel: uses}
  - {to: docs/design/agent/tools.md, rel: uses}
---

# External files

A file link someone sends Nora, from a provider such as a shared drive, is recorded as a **link
handle**: the provider, the provider's file id and the link as posted, and nothing else. Nothing is
fetched, read or remembered about the file until someone asks her to open it. A file lives at its
provider, changes over time, and is Nora's only through the link.

## Parts

- **The handle** replaces the link in the message the model reads with words naming what the link
  points to and a short code, for example "a document link" and its code. The words come from the
  link alone, and the code hashes the provider and file id, never content, so it stays the same while
  the file changes and the same file posted twice is one code. The model never sees a provider file id.
  A link carried inside a redirect or wrapper URL is found, and the whole wrapper is the handle.
- **One neutral tool** opens a handle or a pasted link and reads one part of the file as text into the
  turn. It names no provider and uses the shared result states ([tools](../agent/tools.md)).
- **A file adapter** per provider recognises that provider's links without a network call, describes a
  file, reads it and fetches its bytes. Nothing above the adapter names a provider, and a new provider
  is a new adapter behind the same tool. With the feature off, links are still recorded as handles and
  an open is unavailable.

## What an open does

| asked for | the open |
|---|---|
| to read it here | returns one part of the file as text into the pass |
| to work on it | copies the file into the scope's inbox ([sandbox](sandbox.md)); the copy is a labelled snapshot |
| to see a picture or video | looks at the bytes, never kept |
| to make something from it | nothing new: Nora reads it, then makes the result with her ordinary tools |

Every adapter maps its files onto one vocabulary, so the model never sees a vendor's types:
**document** as structured text, **spreadsheet** as every sheet, **presentation** as text by slide,
**pdf** as text by page range, and **other**, which is copied and not read. A long file is paged and
says which part it is and how many there are; the first open also learns the name, kind, owner and
last-modified time, which ingest never knew.

## Rules

- **Only an ask opens a file.** Not ingest, not a reflection pass, not a background job.
- **Read-only at the source.** Nora never writes, comments on or shares the file, and the credential
  is read-only.
- **Never cached.** Nothing fetched through a link is kept: not content, bytes, a reading or metadata,
  because a linked file is live. Every open fetches afresh, and a snapshot copy is labelled with the
  provider's modified time so Nora can say how old it is. What enters memory is what she says in
  reply.
- **No credential enters the computer.** The bytes are fetched on Nora's side and written into the
  inbox under the ordinary import limit and lease; a slow provider never holds the computer.
- **File text is untrusted.** It enters one labelled, escaped, clamped block and is never an
  instruction; name, kind and modified time travel as typed fields outside it. Parsing runs apart from
  Nora's process, with no secret, a deadline and a memory cap.
- **The answer names the handle, never the file id or the link.**
- **States are specific.** A file not shared with Nora is refused with the way to fix it, which a
  provider may not distinguish from a file that does not exist; a deleted file is nothing found; a
  file too large to read as text is refused and can still be copied; a provider API that is not
  enabled or a revoked credential is unavailable with its cause for the operator, never "not shared".
- **A shared file is a Nora account's file.** One identity of her own, with a read scope per
  provider; an undeclared write scope in a grant makes every service unavailable.

## Not in scope

Writing to or sharing the source; searching everything shared with her, since a link someone sent is
her way in; fetching anything at ingest; a mail or calendar reader as Nora.

## Why

- **A handle and no content** because the file changes, and a cached copy is a second truth that
  ages without a signal.
- **An adapter behind a neutral tool** because the tool's selection and reporting should not change
  when a provider is added.
