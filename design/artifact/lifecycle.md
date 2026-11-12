# Publication and lifecycle

## Room scope

A document belongs to the room and requesting person who produced it. Read
access is evaluated from current policy: the creator and people named by that
room may read it; a wildcard room names nobody, so only the creator reads.
Policy revocation takes effect without a process restart.

An unknown document, a document in another room and a document the viewer may
not read share one not-found answer. Room labels are shown only to viewers who
belong to the room; platform conversation ids never appear in payloads.

Revisions are append-only snapshots. A revision names the base it read and a
stale base conflicts rather than silently winning. The document's latest
revision is its current form; earlier revisions and their media stay readable
under the same gate.

## Links and reads

Document links are minted from a public origin and an opaque handle resolved
among documents the viewer may read. No origin means no URL is returned and no
host or path is invented. Handles are addresses, not secrets; visibility still
decides what resolves.

The document block supplies a small current index of the room's documents with
title, id, revision and age, plus a bounded put-away section for restorable
work. A revision begins with `document_read`, not a remembered title or id.
A failed read renders unavailable, never an empty index that claims no
documents exist.

`document_publish` returns a link only after storage. The delivery layer may
append or repeat a tool-returned link; it never accepts a model-authored
handle. A made-up link is removed, and an empty result is refused.

## Lifecycle and history

Archive removes a document from the ordinary list and room index while keeping
its direct read. Delete hides it from every person-facing read while retaining
revisions for restoration. Active is a new state transition, not an edit to an
old row.

Restoration publishes an earlier spec as a new revision and rechecks it. It
cannot bypass a stale base, archive, deletion or a visibility refusal. Earlier
content is not redacted by editing it out of the current revision; hiding or
forgetting governs the whole document and its history.

Portal state changes use the same read gate as the write gate. A delete from
chat is restorable, while a forgotten document cannot be restored through Nora
or the portal.

## Presentation and media

The portal lists and searches current revisions, renders them with the same
registry and offers ordinary, present, print and history views. These are views
of one stored spec, not new formats. Print uses the reader's browser rather
than a second server renderer.

Pictures resolve through the room's message sightings and media rows, including
web images Nora deliberately fetched and kept. A reference resolves to full
content hashes only after the room and media kind are validated; unknown,
ambiguous or unreadable resolution fails closed. Coordinates in a Map are
resolved by the service from place names, never copied from a person's live
position.

Media bytes are served only when the document and revision reference them and
the viewer may read them. Responses are raster-only, private, non-sniffed and
sandboxed; a missing or unreadable store is unavailable, not a broken-image
claim.

Long background research reports belong to delegated work, which returns a
document id through the same publication and read gates. They do not change
what a document is.
