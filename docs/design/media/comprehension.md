---
class: design
subject: media reading, one model pass per item, rendered by one join
owns:
  - media reading: "what a held item shows, as one perception claim with three levels of detail"
  - reading join: "the one shared step that puts an item's reading beside its handle in every presentation"
area: media
links:
  - {to: docs/design/media/custody.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/context.md, rel: uses}
---

# Reading

## The gap it closes

Someone sends ten photos of a meal and the room replies «where is it?» and
«how much?». Without a reading the record holds every reply and none of the
subject: the marker says a photo was sent, not that it was a meal. The marker
stays; what it points at stops being empty.

## One read, three levels of detail

A reading is a perception claim, inferred and cited to its item
([graph](../cognition/graph.md)). One model pass writes all of it:

| level | is | used by |
|---|---|---|
| **title** | what the thing is, a few words, as a person would name it | transcripts, search |
| **description** | what it shows, a sentence or two | transcripts, the read tool |
| **content** | everything legible or audible in it, in full: the writing on a whiteboard, the words in a voice note | the read tool |

Reading the bytes is the cost; asking the same call for all three is nearly
free, so the stored reading is unbounded and the bound lives at render. The
reading is keyed by the bytes, so the same item reads once for every room.
An item with no reading is an index entry that claims nothing, and it is
distinguishable from one whose reading came back empty.

One mechanism serves every medium, stickers included. What stays with custody
is a platform's own sticker identity, so a repeat sighting costs no platform
call. The reader's prompt is chosen by the item's label, a fact the system
knows from the type and the platform, never anything a room typed.

## Off the ingest path

Custody is inline because a handle is a promise
([custody](custody.md)). A reading is not: an unread item is the state every
item was in before it, so a reading that lands late or never makes nothing
untrue. Reading inline would also stall the whole room behind a video. The
reader fetches the bytes from custody, which is also what the handle promised.

**A voice note's words are the exception.** They are conversation content, not
a description, so they are recorded as the item arrives, never queued behind a
later pass. The pass that follows adds a title and a description from those
words with a cheap text call, not a second listen; if it fails, the words
remain without a title.

## One join renders it

A presentation shows an item as its handle and its reading, for example
`[image a3f2c1: a plate of noodles - …]`. Every presentation that shows items
does it through one shared join: the engagement prompt, the fire path, the
debrief, the reflection loop's windows and the transcript read tool. A
presentation that renders its own copy drifts, and a bare handle beside a
joined one does not read as a formatting difference but as «nobody has looked»,
a claim about the record.

- **Scope comes from the record, never from text.** The join resolves an item
  through the media the row recorded. A handle or label a sender typed never
  resolves, so a room cannot render another room's reading or switch off a
  bound by typing a label. A label in a body may refine the recorded type,
  never contradict it.
- **The bound is at render.** A title and description are clipped to a render
  budget. A voice note's full content is rendered unclipped, since clipping it
  would stop Nora hearing what people said. Any caveat on a reading is
  appended last, after the clip.
- **Checks run where a reading is shown**, never only where it is stored: a
  check applied before the store is one a hit on the stored reading skips.
  When content checks are enabled ([boundaries](../cognition/boundaries.md)) a
  hit costs that item's detail and leaves its handle.
- **An unreadable index is unavailable**, never nothing: the join falls back to
  bare handles. A window with no reading yet is nothing found.
- The join is a read taken at assembly, not inside the renderer, so a prompt
  stays a pure function of its input and can be rebuilt from its trace.

The set of presentations that show items is derived from the source and gated,
so a new one cannot ship unjoined. One that does anyway would show the join
belongs inside the read itself.

## The read tool, in two layers

**Layer one serves the stored reading**: title, description and untruncated
content, with no model call. It is the same reading the transcript rendered a
line of, so the tool cannot contradict the prompt. A voice note reaches only
this layer; its content is the words.

**Layer two answers a question.** It reads the item again with the caller's
question, bypasses the stored reading on the way in and writes nothing back. A
stored reading is a function of the bytes; an answer is a function of the bytes
and the question, and writing it back would poison the item for every room.
The question is the one supplied text in a read, so it is data inside an
authored frame, never a prompt of its own, and it carries only the minimum
public question ([boundaries](../cognition/boundaries.md)).
