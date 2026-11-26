# Observation, Rollup and wakes

## Record everything admitted

Observation is code. Platform adapters push what they can; collectors poll
sources that cannot push. Ingestion records messages with edits, reactions and
deletions, presence and collector health, social items, play, perceptions of
media, and Nora's own sends, decisions, tool results and artifacts. Recording
is unconditional within the admitted source scope; no model chooses which raw
events deserve to exist.

Perception describes a media item and carries its own confidence. Face matching
may identify only a labelled person; an unmatched face stays unknown. Only the
subject or the operator may supply a face label. Non-member biometric vectors
are transient, and media custody remains attached to the source room.

Social observation is limited to admitted members and their opted-in accounts.
A tagged or commenting stranger may remain a handle on a member's item but is
not crawled or read as a member. Live social lookups and the recorded social
stream are different reads; the latter is what Cognition can cite.

Source health is itself observed. Missing data during an outage is not proof
that nothing happened.

## Rollup is a deterministic projection

The Rollup answers what the Record adds up to over an interval. It contains
bounded episodes such as stays, co-locations, outings and responses, and
aggregates such as routines and relationships. It is computed entirely by code,
including when its input includes model-produced perceptions or source-tied
readings already stored in the Record.

A Rollup is a pure function of the Record through a logical watermark. An
incremental build and a full rebuild at the same watermark agree. Time inside
the projection advances through observed events and successful health probes,
not through an unobserved wall clock. Builds are versioned and rebuildable;
Notebook citations resolve by semantic key and interval rather than depending
on a surrogate row surviving a rebuild.

A Rollup may consume another Rollup, never the Notebook. This keeps evidence
flowing in one direction and prevents a model's prior conclusion from becoming
the source of its own reinforcement.

## Stimuli

A stimulus is a durable per-unit reference to something that changed: an
observed item, a Rollup change, a clock occurrence or a self-wake. It carries
its source reference, room or person association where one exists, deterministic
cues and the time it becomes eligible. It is not a conclusion and not an
instruction to act.

Rollup changes ride with a wake started for another reason. A deviation or
co-location is therefore context for judgement, not a special rule that forces
speech. Absence can become a stimulus only when every source needed to prove
that absence was healthy over the relevant interval.

Nora's own sends do not wake her by themselves. Their outcomes and people's
responses remain observable Record, which prevents a loop of self-generated
work while preserving evidence for later reflection.

## Wake policy

The evaluator considers a unit when observed input has settled, a strong cue
needs a quicker look, enough work has accumulated, the oldest input has waited
too long, or a clock or self-wake occurrence is due. These are reasons to
consider a session, not reasons to send a message.

The brakes are single-flight, global concurrency, cooldown, failure backoff,
spend limits and an empty agenda. A bounded read cuts input into deferred work,
never discarded work. No model runs to decide that an agenda is empty.

A wake claims a set of stimuli rather than advancing an undifferentiated range.
Per-row claims preserve work that a bound or a kind-specific wake left behind.
Only a closed outcome permanently consumes a claim. Durable rows, not process
memory, reconstruct pending work and in-flight state after restart.

## Self-wakes

Cognition can request a later look at named subject rows. A self-wake stores
the reason and due occurrence; when due, the session reads the subjects as they
are then plus newer evidence. The old reason is context, not a conclusion to
reassert.

A self-wake may be moved or cancelled with its history retained. One live wake
per subject prevents duplicates, while bounds and ordinary admission prevent
self-scheduling from bypassing the brakes. It has no room-delivery capability.
