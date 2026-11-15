# Observation

Observation is the code that records everything admitted as items, derives
aggregates from them and turns changes into stimuli. No model decides what is
recorded.

## Sources

| source | arrives by | becomes |
|---|---|---|
| platform events: messages and their edits, reactions, deletions; presence; social items; play | the platform pushes | items |
| sources that cannot push | a collector polls | items |
| outside sources a maintain intention watches | the watch poller polls while the intention holds | items citing the watch |
| Nora's own acts and tool outcomes, silence included | the executor that performed them | items |
| source health | probes | items |

Every item of Nora's own acts carries the pass that produced it, so her sent
rows render with that pass and a pass's items can be opened whole. What a pass
expressed — the drafts and conclusions Nora wrote — is among its items; hidden
reasoning is never kept, and what it decided is an intention.

A fetched outside page is an item with its fetch provenance. It can support
public attributes of a place, work, event or organisation, never a private
person's attendance, traits or relationships.

A perception reading of a media item is not observation: it is a claim,
written in orient and cited to the item.

**Rules**

- Recording is unconditional within the admitted source scope.
- Social observation covers admitted members and their opted-in accounts; a
  stranger on a member's item stays a handle and is not read as a member.
- Face matching identifies only a person labelled by themselves or the
  operator; an unmatched face stays unknown, and a non-member's biometric
  vector is transient, never stored.
- Media custody stays with the source room.
- Source health is observed: missing data during an outage is not proof that
  nothing happened.

## Aggregates

An aggregate is a pure function of observed and computed rows at a logical
watermark: episodes (stays, co-locations, outings, response windows),
aggregates (routines, relationships, counts) and baselines.

- An incremental build and a full rebuild at the same watermark agree.
- Projection time advances by observed events and healthy probes, never by an
  unobserved wall clock.
- A citation of an aggregate resolves by its semantic key and interval, so it
  survives a rebuild.
- Verdicts, track records and support tallies also read the conditions and
  rests-on edges of the intentions they evaluate. No computed row reads a
  claim's content: belief never feeds what code derives.

## Stimuli

A stimulus is a durable, per-unit reference to something that changed, with
its source, its room or person where one exists, deterministic cues and the
time it becomes eligible. It is not a conclusion and not an instruction.

| stimulus | from |
|---|---|
| a new item | record |
| a watched source changed | a watch result item |
| an aggregate crossed its baseline | compute |
| an intention due, a review time, a valid-to time ending, a deadline passing | the clock |
| a verdict written | study's verify |
| a person's correction aimed at Nora | record, by an addressing cue |

**Rules**

- Absence is a stimulus only when every source needed to prove it was healthy
  over the interval.
- Nora's own acts never stimulate by themselves; what people do next does.
- An aggregate change rides with a wake started for any reason; it is context
  for judgement, never a rule that forces speech.
