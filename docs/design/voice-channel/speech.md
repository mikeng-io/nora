# Speaking

## Final words, spoken late

The Speaker port turns Nora's final reply into audio. The composition root
selects a provider; the room and agent layers do not know its vendor or wire
format. Synthesis returns audio with its media type or an unavailable result
with a reason. Empty input is not an outage.

The configured provider speaks text Nora has already decided is fit for the
shared room. That deliberate outbound call differs from local transcription of
unknown inbound audio. Cantonese voice and language settings are provider
configuration, not doctrine or a second reply-writing surface.

Speech reaches the audio controller through its bounded, generation-scoped
stream command. The controller, not the Speaker or music manager, owns
preemption and playback. A spoken reply is still associated with the authored
text and ordinary delivery audit; audio markup never becomes her recorded
message.

## Speakable content

Voice is a summary and conversational channel, not a transport for opaque
machine tokens. URLs, handles, hashes, coordinates, long identifiers and
similar strings are not read aloud. The reply should name what the thing is
and point to the text surface for its exact payload.

A deterministic speakability gate enforces this boundary before synthesis. A
reply that cannot safely be spoken falls back to text; a regular expression or
model's good intention is not permission to pronounce an identifier. Listener
volume remains independent of speech priority and any future mixing gain.

## Late speech formatting

Formatting belongs at the speak seam, after the authored reply is final. It
may deterministically remove emoji, Markdown and unpronounceable symbols. An
optional lightweight model may add only provider-supported pause and
interjection markup; it never sees licence to write a different answer.

A code check compares the candidate with her normalized words. Every letter,
digit, combining mark and numeric form must survive in order, and every added
marker must be valid and bounded. Pronunciation overrides are static
configuration rather than model-authored text.

A refusal, empty answer, malformed markup, timeout, truncation or missing
instruction speaks the deterministic normalization instead. With formatting
disabled, synthesis receives the reply exactly as written. The typed fallback
always remains the original chat text, never the marked-up synthesis copy.

## Failure behaviour

Transport failure may use a documented equivalent host, but a provider refusal
is not retried as though it were a routing failure. HTTP success also requires
provider-level success and nonempty decodable audio. No exception from
synthesis or formatting drops the underlying turn.

If the speaker, voice session or controller is unavailable, or playback cannot
be accepted for the current generation, delivery falls back to text where the
platform permits it. An unavailable speech result is never reported as audio
that was heard. Queue and decode bounds apply to active and waiting speech as
well as each individual request.
