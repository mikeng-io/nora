---
class: design
subject: media generation, making an image or a video and the references it may use
owns:
  - video desk: "the spend gate that allows one clip per turn and a durable daily allowance per room before any provider call"
  - canonical self image: "Nora's one reference picture, the only held media that crosses a room's custody"
area: media
links:
  - {to: docs/design/media/custody.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Generation

Nora makes an image or a short video from words and, when permitted, from
media she already holds. Making one is an act through the common envelope
([acts](../cognition/acts.md)): its outcome is an item, and a finished result
is a media item in custody before it is delivered.

## Images

The model decides whether Nora belongs in the picture and which supplied images
show which people. Code enforces custody and reference validity and never
guesses from a phrase: pronouns are ambiguous, and an unwanted reference spends
budget and changes the picture. When several supplied photos could be the
intended person, Nora asks. When an edit depends on a supplied photo, the photo
first passes through the real [read lane](reading.md); it is never imagined from
a filename or the chat around it. Time, timezone and place visible in a picture
come from the turn's actual situation, written out for the image model.

## Video

Creation is asynchronous. A turn starts one provider job through the job
runner and returns; the finished clip later reaches the same room as an
attachment. A render is slow external work with no model running while it
waits, so it is one kind of external job and not a reasoning sub-agent.

- **The tool** binds the request to the current room and requester. The model
  supplies creative intent, never deployment settings or a destination.
- **The video desk** gates spend. It reads its allowance from durable job state,
  so a restart cannot reset it; checks it atomically with creation; and is
  unavailable when the count cannot be read. A repeated call in one turn
  returns the first outcome.
- **The backend** owns the provider's vocabulary and reports a typed outcome.
- **The job runner** owns persistence, recovery and deferred delivery. A job
  survives process recovery because its provider handle and launch time are
  durable.

A create the provider refused spends nothing. One that may have reached the
provider without a followable handle is the act envelope's uncertain outcome:
it counts against the allowance and is never retried blindly. Only a task the
provider explicitly failed is free; a timeout or lost delivery may still have
produced a billed clip.

**Delivery.** The poller downloads a finished clip only once it holds the room's
delivery slot, so a blocked room costs no fetch. A valid clip enters custody
and is attached by its handle, which keeps it available after the provider's
signed link expires. A clip that is invalid, oversized or past a bounded retry
falls back to the signed link, attached by code and never copied by the model.

## References and consent

A reference is a held photo, clip or voice note with a declared purpose:
identity or setting, motion or camera, voice or sound. The only sources are the
room's media window and Nora's canonical self image, never an arbitrary URL.

- Each kind of media has its own permission gate. A person's face and voice
  share one boundary: they are used only for the use their owner asked for.
- Reference mode and first-frame mode are exclusive. References support a newly
  written scene; a first frame is a photograph that comes alive, and is always
  a photo.
- Input bytes live in memory for preparation and creation. Durable state keeps
  the prompt, the custody hashes and the settings actually sent, never bytes.
  References are normalised before upload so provider limits hold locally and
  source metadata does not cross the boundary.
- A prompt describes the media actually passed, reads held media before it
  describes them, and never invents what a reference contains. The runtime
  skill owns that judgement; the backend owns the provider's syntax.

## The canonical self image

Nora's canonical self image is one operator-supplied picture of her
([identity](../appearance/identity.md)). It is a reference only when she is in
the requested picture, and it preserves recognition, not a composition to copy.
A person's photo is that person, never Nora.

It is the one held item that resolves across rooms, and the exception is
narrow: only a full handle whose recorded role is exactly the self role, and
only when no match exists in the room's own media. A short handle resolves to
nothing. No URL or data reference is accepted, and no other role, operator
photo or character is exempt. Resolving it permits use of bytes Nora already
holds; it widens neither what may leave a room nor which provider is used.

When reference images are off, the canonical fallback is off too: her written
anchors may guide a text-only prompt, but no canonical bytes travel. A missing
or ambiguous reference is nothing, never a guessed blob and never a reason to
attach a different person.
