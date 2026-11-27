---
class: design
subject: presence sources, the spatial collector, its episodes and digital activity
owns: [spatial collector, episode emission, digital activity]
area: presence
links:
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
---

# Presence

Presence is where people are and whether they are online, playing or listening.
[Observation](../cognition/observation.md#aggregates) defines the **presence head**,
folded per person from every source's latest items, with the source, time and health
of each part. This bundle owns the two sources that feed it: the spatial collector
and digital activity.

## The spatial collector

The collector is a separate process from the agent. It reads people's positions from
a provider at a cadence, and it is the only holder of the provider's credentials and
the only owner of raw position fixes. It shares a database with Nora and nothing
else, so either can be down without the other.

- **It records a change, not a repeat.** A fix is written only when a person's
  position advances; a phone that holds still is one fix, not one per poll. The fix
  keeps both the age of the position and the age of the observation, and what she
  reports is the position's age.
- **Cadence is adaptive.** It reads often while anyone is moving, backs off while
  everyone is still, and backs off exponentially after a failure with a ceiling, so a
  rejected credential is never hammered.
- **Known states are not gaps.** Sharing off, no fix and a failed read are distinct
  records. A failed read is never summed with a person having stopped sharing; it is
  the source being unhealthy, and an absence read through it proves nothing.
- **Health is recorded per poll** as source health, and a collector that never
  succeeded shows a climbing age rather than no signal, so "not running" and "up but
  never collecting" look different.
- **Circles are selected explicitly** in deployment configuration. Each fix resolves
  its provider account to an identity under the active backend, and managed
  collection does so under the mutation boundary ([identity](../people/identity.md)).
  An account that does not resolve is uncollected. A provider observation never
  registers an identity or grants access.
- **Current roster observations are volatile.** They expire and vanish on a source
  failure or a restart; historical fixes cannot restore them. They are served
  through an authenticated service-private read without credentials or coordinates,
  and a failed source supplies no file fallback.
- Saved places are static landmarks refreshed on a slow schedule that never outpaces
  the back-off.
- A live read may ask the collector to refresh one person now. The collector paces
  that request against its own back-off, never past it.

## Episodes: what the graph records

Fixes run to thousands a day and stay with the collector. **The graph records only
the episodes the collector emits**: a stay began or ended, a trip, an outing. It is
episodes, never fixes, that enter through intake with their registry rows
([observation](../cognition/observation.md)), and only episodes stimulate: arriving
at or leaving a place, starting or ending travel.

- A fix is never a graph row, an edge or a stimulus. The interval of fixes behind an
  episode is provenance in the collector's store, not an edge.
- A stretch with no fix because the source was unhealthy is not evidence that a stay
  ended: absence stimulates only when the source was healthy over the interval
  ([observation](../cognition/observation.md#stimuli)).
- Routines, co-locations and baselines are aggregates computed over episodes
  ([observation](../cognition/observation.md#aggregates)); where a person is going is
  a guess she writes from their routines, never presence.
- Raw fixes are never indexed, rendered or disclosed. Only computed episodes are a
  read surface, and a coordinate never reaches model prose or an outside query
  ([boundaries](../cognition/boundaries.md#egress)).

## Digital activity

Online status, play and listening are the digital half of presence, from the same
head and the same rules, never confused with where someone is. Sources are a game
platform's public profile reads and a chat platform's presence: per-person status,
what they are playing or listening to, and which voice channel they are in.

- Each source enters through an intake adapter. It records a change of state, never a
  repeated read, so a status that flaps does not flood the graph.
- **Away is never flattened to online.** A profile that hides what someone is playing
  is hidden, not offline, and she never claims "not playing" when the field is merely
  withheld. Voice-channel membership is a narrow fact, never the meaning of online.
- A source that cannot be read is unavailable, never nothing found. A person with no
  linked account on a source reads as nothing found for it, never a guessed identity.
- **The audience is the room.** A live read answers only about the invoking room's own
  members; a name outside the roster is filtered, and asking about an outsider reads
  as nothing found. That audience already sees each other's presence, so it carries
  no location guard. In a direct message it answers only about the asker.
- A live read is the present instant and never renders as history. History is
  episodes and aggregates.

## Using presence

A pass sees the head of each person it concerns in its floor, so a request can be
related to where they are without being asked to look. A claim about someone's state
is checked against live presence before it is spoken: a note saying someone is
unwell is not repeated aloud while they are mid-game. Presence is context for
judgement; it never forces speech.
