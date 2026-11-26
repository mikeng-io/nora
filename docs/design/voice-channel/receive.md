# Receiving speech

## Wake-gated input

The receive sidecar uses Discord's protocol stack to join through the audio
controller, associate speaking packets with a user, decrypt the per-user
stream and forward PCM to the wake gate. It performs no wake decision,
transcription or agent reasoning.

The wake gate listens for Nora's wake phrase on the onset of speech. It does
not need to recognise the following command as part of the phrase. Once woken,
it collects one bounded utterance, detects its end and sends that audio to the
local transcription service. Transcription selects the configured local engine
without sending the audio to a cloud vendor. Voice notes and wake utterances
choose their engines independently; an explicit failed engine is unavailable.

The transcription service owns the resident Qwen worker and standalone speech
detector. One serialized slot owns load and inference, and shutdown reaps both
children. Valid no-speech skips decoding; detector failure cannot become silence.
Long speech is partitioned into bounded contiguous windows, and native
Traditional text is preserved without inventing word timestamps. GPU grants
are derived from validated deployment configuration, separately from credentials.

The resulting text enters the ordinary voice-utterance ingest endpoint and
continues through identity, admission, context assembly and response as user
input. It is not Nora's own state or self-representation. A voice turn may
answer through the speaker; an ordinary proactive turn remains text unless its
occasion explicitly grants voice.

Nora does not passively transcribe a channel. Casual speech before a wake is
neither stored as text nor offered to the agent. Barge-in would require a
separately grounded way to distinguish a human from Nora's own synthesized
audio; it is not implied by having a receive path.

## Identity and room scope

The sidecar's user identity comes from Discord's speaking-user mapping, not
voice recognition. The existing identity stage resolves that account to the
same person as a typed Discord message.

The audio connection announces its guild. Nora maps that guild to the one
policy room designated for voice and retains channel information only as
optional provenance. A voice channel therefore shares the text room's
conversation, membership, memory and culture. An unmarked guild is refused
before ingest, and one guild cannot have multiple authoritative voice rooms.

Everyone in a voice channel must understand that the joined bot can receive
its audio. Decryption for the bot does not create permission to disclose what
was heard elsewhere; ordinary room and disclosure boundaries continue to apply
to the transcript and response.

## Custody and failure

PCM is consumed as bounded frames and dropped. Only the woken utterance reaches
transcription, and its audio is discarded afterward. It receives no media
object, content hash, description pass or replay surface. This differs from a
voice note, where the audio is itself the retained message artifact.

Per-user detector state and decoder subscriptions expire. Backpressure drops
frames and records discontinuity rather than building a recording backlog. A
missing wake model is healthy but idle: frames are consumed without scoring,
not treated as detected speech.

No wake means no transcription cost. A transcription or ingest failure cannot
create a user message or a spoken answer, and a failed downstream does not
accumulate audio for later replay. Health distinguishes process liveness,
model availability, received frames, wakes and completed utterances so silence
is not mistaken for proof that the path worked.
