---
class: design
subject: media custody, how held bytes are named, promised and scoped
owns:
  - custody completes before the record promises bytes: "a handle exists only for bytes Nora holds"
  - media handle: "the short prefix of an item's hash that a transcript cites it by"
  - media label: "the kind of an item (image, voice note, audio, video, document) drawn from its registered media type"
area: media
links:
  - {to: docs/design/media/README.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/storage.md, rel: uses}
---

# Custody

## Why Nora holds the bytes

A platform's own process downloads attachments into its own store, under its
own retention. If the record only pointed there, pruning or re-linking that
process would leave a row claiming an image that nothing can produce. So the
bytes are copied at arrival into a store Nora owns, and the item's row is
written with the hash that names them. The record is the record for media
as it is for words.

## Naming

- **The key is the hash of the bytes**, never of the metadata. The same meme
  forwarded five times is one blob; a blob can be checked against the key that
  names it; a rename moves nothing.
- Type, size and the sender's filename ride beside the key and never produce it.
- The **handle** is a short prefix of the hash, a name to cite an item by. It
  is not an identity: the full hash is what a lookup resolves.
- The **label** comes from the registered media type, never from a prefix test.
  Nora can tell a voice note, which a human spoke, from other audio, and a
  video from a spreadsheet.
- A **filename is sender text of unbounded shape**: it may hold a number, an
  identifier or an instruction. It is recorded beside the hash and never
  rendered.
- Custody produces a type and a key and puts no bytes in a prompt. Learning
  what an item shows is a [reading](comprehension.md).

## Custody completes before the record promises bytes

A handle in a transcript is a promise that its bytes can be produced. Custody
therefore runs inline, between identity resolution and the record write, and
its result lands in the same write as the row. Spawning the copy would write
the promise first and keep it second.

The cost is that ingest from one source waits while bytes move. It is bounded:
one short call, a ceiling on size, and no blocking of delivery or health.

An attachment whose bytes do not arrive leaves the body as the platform wrote
it, bare label and no handle, and the message is recorded in full. Every
outcome is counted by kind: stored, no store wired, nothing to fetch, gone at
the source, source unreachable, unreadable answer, over the ceiling (a policy,
not a failure) and store refused. Attachments seen are counted separately from
attachments stored, so a count of zero tells custody being off apart from
nothing having arrived.

Bytes come from the platform adapter's own fetch, never from mounting the
platform's store into Nora: she stays out of the account's identity keys
([platform](../platform/README.md)).

## The store

Custody sits behind a put-and-get port keyed by hash. A filesystem implements
it beside the record, so a row and the blob it names are backed up and removed
together. Swapping the implementation changes no stage, no schema and no
recorded key, because the key is the content.

Retention is [storage's](../cognition/storage.md): past an item source's
horizon the content is deleted and the row becomes a tombstone. Bytes that no
row names are not kept.

## Scope

Custody stays with the source room. A held item resolves only in the room it
was born in; a room cannot reach another room's media by guessing a handle. One
narrow exception, Nora's canonical self image, is in [generation](generation.md).
