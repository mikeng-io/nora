# Platform observations

Platforms report more than utterances: reactions, collective choices, shared
artifacts and changes to rooms, membership and pins. Nora preserves those facts
as typed evidence with explicit coverage, then exposes one consistent current
reading instead of guessing social meaning from raw payloads.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fplatform-observations%2F%22)

## Parts

- **Bridge capture** maps supported native events into one typed observation
  contract beside ordinary messages. It owns source identity, native ordering
  and transport recovery; it does not manufacture an utterance for a service
  event.
- **The observation Record and deterministic fold** retain source history and
  rebuildable current heads. [Observations](observations.md) defines objects,
  actors, transitions, snapshots, targets and coverage.
- **The shared read projection** supplies current and historical source state,
  model-safe handles, evidence references and explicit partiality to every
  consumer. [Projections](projections.md) defines that vocabulary.
- **Context and task surfaces** annotate transcripts, retain relevant objects
  beside ongoing matters and answer bounded source reads. [Exposure](exposure.md)
  defines occasion presentation and stimulus ownership.

## Terms and promises

An object is distinct from an observation about it. Creation, revision,
withdrawal, set changes and observed snapshots have different semantics. A
snapshot does not invent intervening transitions; a complete replacement clears
only its covered scope; omitted people, votes or fields remain unknown.

Actor, subject, target and source room remain distinct. Anonymous counts do not
invent voters, a departure does not infer intent, a reaction does not prove
agreement, a vote does not prove attendance and closing a poll does not settle
the related matter. Guild visibility is not channel audience.

Typing is not evidence: it is not persisted, rendered or allowed to wake model
work. Presence, read receipts and passive social collection do not expand merely
because this contract exists. Raw operational traces never become social
evidence.

Coverage differs by platform, account mode, permission, room mode, source
version and field. Unsupported, unimplemented, disabled, unavailable and not
inspected are different states. A source capability is not capture, a stored
payload is not a projection, and a projection is not model visibility.

## Change and failure

New platforms join through mappings and generated native fixtures, not new
cognition workflows. New object vocabulary needs a real journey and an owning
store; it does not justify a generic event lake or a universal query tool.
Existing message, media, graph and Notebook owners keep their responsibilities.

Replay does not duplicate observations or counts. Late and ambiguous ordering
remains visible instead of overwriting newer state. Unknown targets stay
unresolved and link later. Every read distinguishes found, genuinely absent and
unable to check; partial readable evidence carries its limitations rather than
becoming a confident zero.
