---
class: design
subject: media, the images, audio and video Nora holds, reads and makes
owns:
  - media item: "a held image, sticker, GIF, voice note, audio or video, named by the hash of its bytes"
area: media
links:
  - {to: docs/design/cognition/README.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/storage.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
---

# Media

Media is what people send that is not words, and what Nora sends back. She
holds the bytes, learns what they show, and, when asked, makes new ones. Media
adds no memory, store or loop of its own: it is one item source of the
[graph](../cognition/graph.md), and every reading of it is a claim.

## Parts

| part | is responsible for | defined in |
|---|---|---|
| **custody** | holding the bytes under a content hash, in the room they arrived in, and promising only what it holds | [custody](custody.md) |
| **reading** | one model pass that says what a held item shows, stored once and shared by every room | [comprehension](comprehension.md) |
| **read lane** | reading long media in windows, with honest coverage, timeouts and cancellation | [reading](reading.md) |
| **presentation** | putting a reading to the model and to people without retelling it as an inventory | [presentation](presentation.md) |
| **generation** | making an image or a video, and the references it may use | [generation](generation.md) |

## How it connects

```
arrival ─ custody (inline) ─▶ media item + handle in the record
                │
                └─ reading (off the ingest path) ─▶ perception claim, cited to the item
                                                        │
every presentation ─ one join ─▶ handle + title + description        read tool ─▶ stored reading,
                                                                      or a fresh answer to a question
generation ─▶ provider job ─▶ finished bytes enter custody ─▶ delivery
```

- A media item is an item: append-only, with its basis, scope and provenance
  ([storage](../cognition/storage.md)). Its bytes stay with the source room
  ([observation](../cognition/observation.md)), and its retention is the
  item source's horizon.
- A **reading** is a perception claim: inferred, cited to the item, never
  observation ([graph](../cognition/graph.md)). What an image shows is Nora's
  reading of it, not a fact she vouches for.
- Delivering an image or a clip is an act through the common envelope
  ([acts](../cognition/acts.md)); its outcome is recorded like any other.
- Which items a pass may open, and in which room they may render, follow the
  unit as the read boundary ([boundaries](../cognition/boundaries.md)).

## Invariants

- The record never promises bytes it does not hold: a handle exists only after
  custody completes.
- A medium Nora cannot read is never described as if she had: unreadable,
  refused and truncated reads are unavailable, never a confident summary.
- Same bytes, same reading, in every room; a reading's place and time of use
  decide only how it renders, never what it says.
- Text that came from a sender (a filename, a caption, a handle or label typed
  into a message) is content, never an instruction and never a key.

## Where it lives in the code

`media/` holds custody, reading and generation. Its items are read by every
area that renders a transcript; the shared join is the one way they do.
