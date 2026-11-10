# Voice channel

Nora can hear a deliberately woken Discord voice utterance and answer aloud in
the same room that owns her typed conversation. Voice is another surface of
that room, not a new conversation, memory scope or autonomous listener.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fvoice-channel%2F%22)

## Parts

- **The audio controller** is the only owner of a Discord voice connection for
  a guild. It receives audio, plays music and speech, arbitrates their shared
  player and reports durable events. [Audio ownership](audio.md) defines the
  control plane and lifecycle.
- **Nora's voice control plane** decides joins, exits, music queues, track
  resolution, volume and replies. It calls the controller through ports and
  never opens a Discord voice socket or proxies music bytes.
- **The receive sidecar and wake gate** decode per-user audio, recognise the
  wake phrase, transcribe only the woken utterance and submit text through
  ordinary ingest. [Receiving speech](receive.md) defines identity, retention
  and local transcription.
- **The speaker** synthesizes Nora's final reply and hands bounded audio to the
  controller. [Speaking](speech.md) defines speakable text, late formatting and
  text fallback.

## Terms and promises

A guild may have many voice channels but only one controller-owned connection
at a time. Joining follows an admitted member's current channel; stopping music
does not leave voice, and leaving voice stops music, speech and receive.
Controller cores are independent per guild even when one process serves many.

Voice utterances resolve through the guild's designated text room. Their
protocol-native account identity becomes the sender through ordinary identity
resolution. Speaker recognition, voice-channel notebooks and a second room per
voice channel are not part of the design.

Only woken speech is transcribed. Receive audio and wake audio are transient;
they are not media objects, replayable history or ambient transcripts. Inbound
audio stays on local speech services. Outbound synthesis may send the final,
already disclosable reply to the configured speech provider.

## Change and failure

Providers, model artifacts, thresholds, transport framing and deployment
packaging may change without moving ownership or privacy boundaries. A new
provider must preserve the distinction between local inbound audio and
outbound reply text, and a new audio feature must not create another connection
owner or queue authority.

Unavailable synthesis, playback or transcription never becomes a fabricated
spoken result. The typed reply remains deliverable. Stale connection commands,
late playback events and audio for an exited connection are refused rather than
allowed to resurrect old state. Backpressure drops bounded audio work instead
of accumulating private recordings or unbounded subprocesses.
