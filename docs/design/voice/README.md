---
class: design
subject: voice, hearing a woken utterance and answering aloud as a surface of a room
owns: [voice is a surface of its bound room, audio controller, wake gate, speakability]
area: voice
links:
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# Voice

Nora can hear a deliberately woken utterance in a Discord voice channel and answer
aloud. **Voice is a surface of its bound room**: it is not a new conversation, a new
memory scope or an autonomous listener. Music plays on the same connection
([music](music.md)).

## Parts

| part | does | never does |
|---|---|---|
| **audio controller** | alone owns a guild's voice connection: receives, decodes, plays, arbitrates music and speech, reports events | decide what to say or play |
| **voice control plane** | decides joins and exits, queues, volume and replies, and calls the controller through ports | open a voice connection or relay music bytes |
| **receive sidecar and wake gate** | decodes per-user audio, listens for the wake phrase, transcribes only the woken utterance | reason, or transcribe unwoken speech |
| **speaker** | turns Nora's final reply into bounded audio for the controller | change what she said |

## The room it is bound to

A guild designates one text room for voice. Every voice utterance resolves through
that room, so the voice channel shares its conversation, members, culture and
understanding. A voice channel is not a second room; an unmarked guild is refused
before intake, and a guild has one authoritative voice room.

The speaker is the protocol's speaking-user account, resolved to an identity as a
typed message's sender would be ([identity](../people/identity.md)); a voice is never
recognised as a person. Decrypting for the bot grants no permission to disclose what
was heard elsewhere, and the transcript and the reply follow the room's disclosure
boundary like any other.

## Hearing

- **Only woken speech is heard.** The wake gate listens at the onset of speech and
  collects one bounded utterance. Nothing before the wake is stored or offered to
  her, and no channel is transcribed passively.
- **Audio is transient.** Frames are consumed and dropped; a woken utterance is
  discarded after transcription. It is not a media object, has no description pass
  and cannot be replayed. A voice note is different: its audio is the retained
  message.
- **Inbound audio stays on local speech services.** Only already-disclosable reply
  text may reach an outbound speech provider.
- The transcript enters through ordinary intake as the speaker's own message
  ([observation](../cognition/observation.md)), then identity, admission and context
  assembly as for any message. It is user input, never her own state.
- A failed transcription or intake creates no message and no spoken answer, and keeps
  no audio for later. Health separates process liveness, model availability, frames
  received, wakes and completed utterances, so silence is not read as proof the path
  worked. Under load, bounded audio is dropped and a discontinuity recorded; no
  recording backlog accumulates.

## Speaking

- Her **final** reply is spoken, late: formatting happens at the speak seam, after the
  words are fixed. It may remove emoji, markup and unpronounceable symbols, and may
  add pause and interjection markers a provider supports; a check requires every
  letter, digit and numeric form of her words to survive in order. Anything else
  speaks the plain normalisation.
- **Speakability.** Voice carries a summary, not opaque tokens. A URL, handle, hash,
  coordinate or long identifier is not read aloud; she says what it is and points to
  the text for its exact value. A deterministic gate enforces this before synthesis;
  a reply that cannot be spoken safely falls back to text.
- The authored text stays her recorded message. Audio markup never becomes it, and the
  typed fallback is always the original chat text.
- An unavailable speaker, session or controller, or playback the current connection
  cannot accept, falls back to text where the platform allows. Unavailable synthesis
  is never reported as heard.
- A voice turn may answer aloud; a proactive pass stays text unless its kind
  explicitly grants voice.

## The controller

- **One owner, two planes.** Nora retains every decision; the controller carries them
  out. A boundary in the code prevents a second connection.
- A guild has at most one connection, serialised per guild. Each connection has a
  generation, invalidated on exit, death or replacement; a command, audio or event
  naming a stale generation cannot act on its successor.
- Whether a restart rejoins is durable desired state, and an explicit exit makes it
  idle. After a restart Nora adopts the controller's live connection rather than
  restoring a competing one.
- Joining follows an admitted member who is currently in a voice channel in an admitted
  guild. Nora does not pick a channel or keep a list of voice-channel rooms.
- **Speech outranks music.** It suspends the current music, plays without overlap,
  then resumes a seekable track near where it stopped; a live stream ends instead of
  pretending to resume. A skip or stop during speech affects only the suspended
  music.
- Stopping music leaves voice connected; leaving voice stops music, speech and
  receive. With no human listener left the connection leaves; Nora does not count as
  a listener.
- Requests carry idempotency identity, and events carry playback identity through a
  bounded outbox. Only the event for the playback expected now advances anything;
  duplicates and late events are harmless.
- An unreachable controller is unavailable, not proof that nothing is playing or that a
  track ended. A missing connection is different again. A stalled decoder ends with a
  failure event and is reaped.
- Mixing policies such as crossfade or barge-in are optional changes to this contract;
  each must keep speech priority, listener volume independent, resources bounded and
  one connection owner. Barge-in needs a grounded way to tell a human from her own
  synthesised audio first.
