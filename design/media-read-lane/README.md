# Media read lane

The media read lane turns bytes already in Nora's custody into descriptions she
can use. It covers video, audio and image understanding, not social retrieval,
voice-note transcription or media generation.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fmedia-read-lane%2F%22)

## Parts

- **Preprocessing** normalizes media into bounded visual and audio inputs while
  preserving source identity, duration and coverage. Source bytes are never
  replaced by a provider's redaction or an invented duration.
- **The describer boundary** selects a model and transport that can accept the
  modality. Providers are independent by modality; a missing credential or
  unsupported content block is unavailable, never a silent fallback.
- **Windowed reading** splits long media into ordered windows and reads them
  with bounded fan-out. Concurrent windows restore temporal order and degrade
  individually rather than failing the whole clip.
- **Audio understanding** keeps speech and meaningful sound in the evidence.
  On-screen text, visible action, dialogue, lyrics and non-speech sound remain
  distinct claims with their own confidence and coverage.
- **Timeouts and budgets** derive from serial waves, not from a count of
  concurrent calls. Tool deadlines include preprocessing, provider waits and
  nested reads, and truncation is unavailable rather than a confident partial
  description.
- **Coverage reporting** describes the actual intervals read, including holes
  and refused windows. It never turns a capped read or a middle gap into a claim
  that only the beginning was seen.
- **Media-facing presentation** gives the model descriptions, ages, roles and
  uncertainty without turning them into facts to recite or a cheaper substitute
  for the full item.

The subjects are [reading and coverage](reading.md) and [media
interaction](interaction.md).

## Invariants

- A provider that cannot accept a modality never receives it as text and is
  never treated as having read it. Transport error is not content absence.
- Concurrent work is bounded by ownership: ingest jobs, tool executor slots,
  per-turn nested reads and other processes. No per-call semaphore pretends to
  be a global provider ceiling.
- One failed window does not erase successful windows, and one truncated answer
  does not become a complete description.
- A description carries its evidence's time and coverage. Older stored readings
  remain historical and are not silently presented as a new model's reading.
- Automatic reads obey the same custody, disclosure and cancellation boundaries
  as an explicit media read. Cancellation stops downloads, child processes,
  cache writes and scratch files.
- Media the sender already sees is not retold as an inventory. A direct request
  to describe gets what Nora noticed, while a practical question gets the fact
  that answers it.
