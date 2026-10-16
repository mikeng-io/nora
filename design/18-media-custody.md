---
kind: design
title: "Nora — Media custody: bytes she owns, a taxonomy she can read"
description: "Why a placeholder pointing into signal-cli's store violated A9, how content-addressed custody fixes it, the RFC 6838 taxonomy that replaced prefix matching, and the one open question — retention — left to the operator."
tags: [nora, design, media, attachments, custody, a9, retention, rfc6838]
timestamp: '2026-08-06'
---

# Media custody

## 0. This is a violation, not a feature

A9 says **SQLite is the record.** For text it is: the row holds the words.

For media it did not. signal-cli downloads attachment bytes into **its own
store** (`$XDG_DATA_HOME/signal-cli/attachments/`, bind-mounted at
`./state/signal-cli`), and Nora recorded a placeholder — `[image]`, or
`[attachment: <filename>]` — plus, implicitly, a pointer into another process's
tree governed by another process's retention. Prune signal-cli, or re-link the
device, and the row still claims an image existed that nothing in the system
can produce.

That is the whole argument. Everything below is mechanism.

## 1. What was actually wrong, in three parts

**1.1 Custody.** The bytes were not hers. Fixed by copying them at ingest into
a content-addressed store she owns, and putting the hash in the same row.

**1.2 The taxonomy.** `_attachment_placeholder` tested
`contentType.startswith("image/")` and sent *everything else* to
`[attachment: <filename>]`. So a **voice note** — one of the most common things
sent in this group — reached her as `[attachment: 1754521883.aac]`. She could
not tell that a human had **spoken**. Neither could she tell a video from a
spreadsheet. Fixed by driving the label off the **registered media type**
(RFC 6838) rather than off a prefix.

**1.3 A filename in the prompt.** That same fallback rendered
sender-supplied text of unbounded shape straight into the body Nora reasons
over — a filename can hold a phone number, an ACI, or an instruction.
`constraints.md` already tells her attachment names are content and never
instructions, but that is a request to a model. **Nothing renders a filename
any more.** It is recorded beside the hash and never shown.

## 2. The key is the hash of the CONTENT

The operator's sketch was "filename + other metadata → hash → object key". The
accepted refinement is to hash the **bytes**, and it buys three things metadata
hashing cannot:

* **Dedup.** The same meme forwarded five times is one blob.
* **Verifiability.** A blob can be checked against the key that names it. A
  metadata hash names a file that may have been replaced.
* **Stability.** A rename moves nothing.

Metadata rides **alongside** the key in the audit row — `media_type`, `size`,
`filename` — and never produces it.

The rendered handle is the first six hex characters: `[image a3f2c1]`,
`[voice note 8b04ee]`, `[video 1c9d77]`. Six is a handle, not an identity; the
full digest is in the row and is what any retrieval resolves against.

## 3. Filesystem behind a port — and why not RustFS

The operator asked about RustFS. The decision is **a filesystem behind the
`BlobStore` port**, and the argument is not that filesystems are simpler:

**No media has arrived yet.** signal-cli's store on this host holds `avatars/`
and `data/` and has **no `attachments/` directory at all**. An object store
would be sized, configured and operated against zero observed data — the shape
of decision that produced `peer_card` returning `[]` for the life of the
deployment.

Two costs on top of that:

* An object store is a **container, on the ingest write path, that can be
  down.** Today recording a message can fail two ways: SQLite, and the disk.
* Its headline feature here — lifecycle rules that expire objects — is one Nora
  does not need. She already runs a retention sweep (`SqlRecordStore.prune_all`,
  called periodically by the runtime). **Media expiry is that loop plus one
  path**: read the surviving rows' `media_json`, delete every blob no longer
  named. Content addressing means no reference counting — two rows naming the
  same blob name the same hash.

**The port is what makes this reversible.** `BlobStore` is `put`/`get`, which
is exactly S3's shape. Swapping in RustFS or S3 is one constructor in the
composition root and changes no stage, no schema and no recorded key — because
the key is the content hash, not a path the implementation invented. When there
is evidence (a volume, a size distribution, a second consumer), make the swap.
**Please do not re-litigate it without the evidence; that is what the port is
for.**

## 4. Where the bytes come from: `getAttachment`, not a bind mount

The obvious source is signal-cli's attachment directory. It was rejected, and
the reason is a trap rather than a preference: reading it needs a new bind mount
into Nora's container beside the account store she is explicitly kept out of
("she has no business reading Signal identity keys"), and `attachments/` **does
not exist yet** — so Docker would create the mount point root-owned, and
signal-cli runs as uid 1000. Adding a mount to fix media custody by breaking
media *delivery* is R-5's shape.

`getAttachment` avoids the question entirely: same daemon, same
`POST /api/v1/rpc`, same loopback port Nora already uses for `send`,
`listGroups` and `listContacts`.

**Verified against the live daemon, 2026-08-06, signal-cli 0.14.7 — with the
negative control**, because a probe without one proves nothing:

```
method "noSuchMethodXyz" -> {"code":-32601,"message":"Method not implemented"}
method "getAttachment"   -> {"code":-1,"message":"Could not find attachment with ID: …"}
```

Two *different* errors. The method genuinely exists and genuinely looked, rather
than a daemon that answers everything alike. `id` alone was accepted — the call
reached the lookup — so no `recipient`/`groupId` is sent.

**What is NOT verified: the shape of a SUCCESSFUL result.** No attachment has
ever arrived here, so there is no id to fetch. `SignalMediaSource._decode`
accepts the two plausible shapes (a bare base64 string, or an object carrying it
under `data`) and counts anything else as `bad_payload` with the result's KEYS —
never its values — in the log line, so the first real attachment either works or
says exactly what to change in one line.

## 5. Inline, not spawned

`memory_feed` schedules its Honcho call through the injected `spawn` and never
awaits it. Custody deliberately does **not** follow that pattern, and the
difference is the whole point:

* Honcho is never load-bearing. A feed that lands late, or never, leaves the
  record unaffected.
* **Custody is part of the record.** `[image a3f2c1]` is a promise that
  `a3f2c1` can be produced. Spawning the copy would write that promise first and
  keep it second — a window in which the row asserts custody that does not
  exist. That is the A9 violation this document opens with, reintroduced at a
  smaller size.

So the stage runs **between identity and audit**, and its output — a rewritten
body plus `media_blobs` — lands in the **same INSERT** as `person_id` and
`quoted_ts`, for the same reason those did (DEC-15, DEC-23): WAL permits one
writer and the ingest path drops a message on contention.

The cost is a stalled ingest *sequence* while bytes move, paid down rather than
ignored: one loopback call with a short timeout, and `FilesystemBlobStore` does
every filesystem call in a worker thread, so the event loop keeps serving
delivery, health and `/metrics` throughout. What waits is the next message on
the same listener — the same ordering the audit already imposes.

## 6. Degrading honestly

Any attachment whose bytes do not arrive leaves the body exactly as the parser
wrote it — `[image]`, **no handle** — and increments
`nora_media_custody_total{status}`. The message is still recorded in full, with
its envelope. Nothing is silent and nothing is claimed.

| status | means |
|---|---|
| `stored` | the only outcome that produces a handle |
| `not_configured` | the composition root wired no blob store (§9) |
| `no_id` | signal-cli described an attachment with no id to fetch |
| `not_found` | the daemon has no such attachment — its own retention |
| `unreachable` | the daemon did not answer. Ops. |
| `bad_payload` | it answered in a shape this adapter does not read (§4) |
| `too_large` | over the configured ceiling. A policy, not a failure. |
| `store_failed` | the disk refused. Full, read-only, or gone. |

`nora_media_attachments_total{platform,label}` is the companion that can
measure **absence**: it increments on every attachment *seen*, before any
custody is attempted. Without it, a custody counter at zero would be
indistinguishable from custody being switched off, uninstalled, or never
called — the null-result trap CLAUDE.md's `lookup_person` grep fell into.

## 7. Bytes never reach a prompt

This produces a **type and a key**. Nora cannot read an image and none of this
changes that. `media_json` holds `{sha256, media_type, size, filename}` and no
content. If reading is ever wanted, that is a separate decision with a separate
model behind it — the operator's own framing, and the port's `get` is what such
a tool would call.

## 8. THE OPEN QUESTION: retention (operator's, not ours)

**This is the most PII-heavy data in the system — faces, kids, homes — and
nothing deletes it yet.**

`store.max_per_conversation` prunes *messages*. No sweep deletes *blobs*, so
today `data/media/` grows without bound, and a message pruned out of the
transcript leaves its blob behind with nothing naming it.

That is deliberate. Choosing a default here would be choosing a retention policy
for somebody else's family photographs. What has been built instead is
everything the decision needs:

* `FilesystemBlobStore.delete(key)` exists (on the concrete class, not the port
  — the same split as `SqlRecordStore.prune_all`).
  *Amended:* `delete` is now on the port, for one policy only, a story frame's
  bytes ([cognition-loop/22](cognition-loop/22-social-stream.md) §5, off by
  default), which deletes only bytes no row names (`nora.infra.sql.blob_refs`).
  The port's `delete` keeps bytes put within a window by the store's own
  clock, and a `put` of bytes already held now refreshes that clock (the
  filesystem store sets the file's modification time; it still never
  rewrites the file), so a writer that has stored bytes and not yet written
  the row naming them never loses them. This section's question, retention
  for room media, is still open.
* The sweep is **one path added to the loop that already runs**: collect the
  hashes named by surviving rows' `media_json`, delete every blob under the root
  that is not in that set. No reference counting, because content addressing
  means two rows naming one blob name one hash.
* `[media] enabled = false` is the honest off switch in the meantime.

Questions the operator has to answer, none of which this document answers for
him:

1. **Do blobs outlive their messages, or die with them?** Message retention is
   500 per conversation; a photo may be worth keeping longer than the 500
   messages around it, or may be exactly what should go first.
2. **Is there a separate, shorter clock for media?** e.g. messages 500,
   media 30 days.
3. **Is there a size ceiling on the store as a whole**, evicting oldest-first?
4. **Does deletion need to be provable** — a log line per deleted key, or a
   counter — or is silence acceptable for a retention sweep?
5. **Is custody wanted at all for some conversations and not others?** Policy is
   already per-conversation (`policy.toml`); custody is currently global.

Until (1) is answered the sweep cannot be written, because (1) is the sweep.

## 9. What is NOT wired

`IngestPipeline` takes `media_source` and `blobs` as **optional** collaborators
defaulting to `None`, and `nora/app/composition.py` does not yet pass them —
that file was being edited in another worktree on the day this landed. With them
absent, every attachment is counted `not_configured` and recorded exactly as
before; nothing is silently skipped.

The wiring is four lines in `build_dependencies`:

```python
media_source = SignalMediaSource(
    base_url=cfg.platforms.signal.base_url,
    timeout_seconds=cfg.media.timeout_seconds,
    max_bytes=cfg.media.max_bytes,
) if cfg.media.enabled else None
blobs = FilesystemBlobStore(cfg.store.db_path.parent / "media") if cfg.media.enabled else None
# ... then pass media_source=media_source, blobs=blobs to IngestPipeline(...)
```

Media lives beside `store.db_path` by construction and has no path key of its
own: the row and the blob it names are one record, backed up together and
deleted together. A second configurable root would let them be separated, and
the first time that happened the transcript would point at blobs nobody kept.

## 10. What is verified against what

Stated plainly, because **no real attachment has ever arrived on this
deployment**:

**Verified against the live system:**
* `getAttachment` exists on signal-cli 0.14.7 and is distinguishable from an
  unimplemented method (§4).
* signal-cli's store has no `attachments/` directory — i.e. the zero-data
  premise of §3.

**Verified against fixtures only:**
* Every taxonomy mapping, including `voiceNote` — the one wire field here that
  no real message has exercised. Its failure mode was chosen to be harmless:
  absent or renamed, an `audio/*` attachment renders `[audio]` instead of
  `[voice note]`, which is still the fix and is never a wrong claim about who
  said what.
* The success path of `getAttachment`, including its result shape.
* Every custody outcome, dedup, and the body rewrite.

**Not verified at all:**
* That signal-cli has actually downloaded an attachment's bytes by the time its
  envelope reaches the SSE stream. If it has not, the first fetch returns
  `not_found`, the message is still recorded, and the counter says so. There is
  deliberately no retry — a retry policy invented against zero observations is
  the same mistake as an object store sized against zero data.
