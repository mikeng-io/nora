# Cognition and the Dreamer runtime

Nora has two execution layers. Conscious turns answer when called and speak in
one room. Cognition is her subconscious: it notices what changed, forms and
revises understanding, and decides whether anything deserves attention. It
never delivers to a room itself.

**Status:** building

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fcognition-loop%2F%22)

## Parts

- **The Dreamer runtime** owns subconscious admission, single-flight work,
  cancellation, model invocation and budgets. An adaptor supplies a job and its
  typed outcome; the runtime does not infer success from a model returning.
- **Observation** records messages, presence, social activity, play, media
  perception and Nora's own acts. Deterministic rollups turn that Record into
  episodes and aggregates. Durable stimuli make changes available to Cognition.
- **The wake evaluator** decides when a unit deserves a session. It claims a
  set of stimuli and records the wake without calling a model.
- **The Cognition session** is one agent per wake. It orients against the
  Record, Rollup, Notebook and permitted external reads, then writes validated
  understanding, inward self-wakes or outward reminders.
- **The conscious fire path** receives due reminders. It reads the room again
  at delivery time and may speak, act elsewhere or remain silent.
- **The data layers** separate authored law, observed Record, computed Rollup,
  judged Notebook and read-time Views. Each has one direction of authority and
  a distinct writer.
- **Live threads** extend the Notebook with movement, expiry and two exits for
  ongoing situations. [Live threads](threads.md) remains designed.

The subjects are [execution](execution.md), [observation](observation.md),
[the session](session.md), [the data model](data.md),
[proactive delivery](proactive.md), and [live threads](threads.md).

## The loop

```text
world and Nora's acts
  -> Record and deterministic Rollup
  -> durable stimuli -> wake and admission
  -> one Cognition session
       -> revised Notebook and memory state
       -> self-wake -> later subconscious eligibility
       -> reminder -> conscious fire turn -> governed delivery or silence
  -> outcomes become Record for the next loop
```

Code decides what is observed, when work is eligible, which tools are granted,
what writes may commit and what may reach a room. The model decides what the
evidence means, whether understanding changed and whether anything is worth
doing. Nothing requires a model call merely because a clock advanced.

## Invariants

- A unit is the read boundary. Room binding controls where an act lands and how
  disclosure is evaluated; it is not a second memory partition.
- Only Record observations and Rollups computed from them are evidence. A
  Notebook entry or third-party memory can guide a search but cannot ground a
  new claim by itself.
- One session holds orient, decide and act together. Splitting those judgements
  across agents would discard the context that makes them coherent.
- The model proposes every persistent judgement. Code validates citations,
  revisions, vocabulary, disclosure and provenance before committing it.
- Cognition has no delivery verb. A self-wake is inward; a reminder is the only
  bridge to a conscious room turn.
- A closed session consumes only the stimuli it claimed. Failure, cancellation,
  expiry or a resource limit leaves unprocessed input pending.
- An unavailable source is not evidence of absence. A stopped or unmeasurable
  step reports unavailable or failed, never a clean nothing-found result.
- Doctrine and runtime instructions are authored. Learned Character, Lessons
  and Standing remain below that law and carry their evidence and history.

## Why this shape

Untagged conversation and changes in the world must be able to revise what
Nora holds; waiting for a direct request leaves her model stale. Consolidation
belongs off the speaking path so a reply never waits for reflection. A durable,
bounded session rather than a periodic completion gives her the reads needed
to understand a situation and the brakes needed to prevent unattended work
from becoming unbounded spend or unsolicited speech.
