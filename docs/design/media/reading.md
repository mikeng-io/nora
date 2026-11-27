---
class: design
subject: the media read lane, windows, coverage, timeouts and cancellation
owns:
  - reading coverage: "the set of source intervals a reading actually read, holes included"
  - read window: "one bounded temporal slice of long media, read on its own and ordered by its source start"
area: media
links:
  - {to: docs/design/media/comprehension.md, rel: uses}
  - {to: docs/design/media/custody.md, rel: uses}
  - {to: docs/design/cognition/execution.md, rel: uses}
---

# The read lane

The read lane turns held bytes into a [reading](comprehension.md) for video,
audio and images. It reads what custody holds and nothing else: not social
retrieval, not voice transcription at ingest, not generation.

## Parts

- **Preprocessing** normalises media into bounded visual and audio inputs and
  keeps the source's identity, duration and coverage. A provider's redaction
  or an invented duration never replaces the source.
- **The describer boundary** picks a model and transport that can accept the
  modality. A provider that cannot accept one never receives it as text and is
  never treated as having read it. A missing credential is unavailable, never a
  silent fallback.
- **Windowed reading** cuts long media into ordered windows and reads them with
  bounded fan-out. Windows degrade one at a time.
- **Audio** is read alongside the picture: speech, lyrics, on-screen text,
  action and meaningful sound stay separate claims, each with its own
  confidence. Words that must be exact are attributed and timecoded.

## Windows

Short media is read whole. Longer media is cut into windows with a stable
identity and a source start. Each window reads on its own and its description
is placed back by source time, never by completion order, so a dropped window
cannot shift its neighbours' timecodes.

A window that is refused, empty, truncated or out of time is an unread
interval: it stays visible as a gap in the result and never joins the described
set. One failure never discards its siblings. A model that failed a window the
cutter produced and a cutter that failed to produce it are both gaps, and both
are said.

The original is stored once. Windows are transient: cut at read time and
discarded.

## Coverage

Coverage is a set of intervals, not a duration. A capped clip, a cutter refusal
and a model refusal combine, and the result says exactly which intervals were
read, middle holes included. A partial read never claims to have seen only the
start unless that is literally true, and an unmeasurable duration is unknown,
not covered.

A ceiling on how much is read is real and is stated, never silent: the clip's
length, how much reached a model and the fact that the rest did not are part
of the reading's provenance and of its description. The caveat is its own part
of the reading, so a render bound cannot clip it away, and every presentation
appends it last. A read that is shortened by anything is never presented as
the whole.

A question-conditioned read of video is a function of the bytes and the window
geometry, so it opts out of any cache keyed on bytes alone. The deliberate look
and the automatic one read the same way; one cannot answer for a clip it saw
less of than the other.

## Time and fan-out

A call has a deadline and a tool invocation has a different, enclosing budget.
An invocation with several windows takes serial waves: concurrent calls overlap,
while queued calls and waves add. The per-call timeout derives from the wave
count, not from the number of items, and the enclosing budget includes
preprocessing, nested reads and queueing.

Fan-out is owned at several levels: window slots per read, ingest jobs, the
shared tool slots and nested reads per turn. No per-call semaphore stands for a
global provider ceiling.

A window that timed out is owed one more attempt while the read's own budget is
unspent; one that answered is never re-asked, and one that stays unread remains
a gap. A look under an envelope the reader cannot otherwise bound passes its
deadline down, so the retry is spent from the same allowance that ends the
look.

Truncation by token limit or time is unavailable. It is never rendered as a
short but complete reading, and no deadline change may blur that.

## Cancellation and custody

Cancellation is awaited: it stops provider calls, downloads, the cutter and
scratch writes before the tool returns, and a cancelled read persists no
reading as if it had completed. Automatic reads obey the same custody,
disclosure and cancellation boundaries as an explicit one.

A look at an item fetched from outside keeps its bytes in fetched custody, born
in the room it was read in, so a later look names the held object by its handle
instead of a signed link that expires, and the same room scoping decides what
the handle resolves to.

A description is not evidence that the source was seen when its read failed or
was withheld. A reading carries its evidence's coverage and the provider, model
and time that produced it, so an old silent reading is never presented as a new
model's.
