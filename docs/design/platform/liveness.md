---
class: design
subject: "how one platform's failure stays on that platform and a degraded platform recovers without a restart"
owns:
  - platform state: the live, degraded or disabled state of each platform
  - degraded platform: an enabled platform whose own identity could not be resolved, which sends and reads nothing until it recovers without a restart
area: platform
links:
  - {to: docs/design/platform/README.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
---

# Liveness

Each platform runs in its own bridge so one transport can fail alone. The app
holds to the same rule: an enabled platform that cannot say who Nora is on it
goes **degraded** alone, the others start, and the degraded one becomes live
again without a restart once its identity resolves.

## States

| state | meaning | inbound | outbound | identity |
|---|---|---|---|---|
| **live** | its own identity is resolved and registered | admitted | sends and reacts | registered, and one of Nora's own aliases |
| **degraded** | enabled, built, identity unresolved | nothing is read: no listener, no stream reader, no work claimed | every send and reaction refused with no attempt | absent; a placeholder is never registered |
| **disabled** | off in configuration | none | none | absent |

## One contract

Liveness belongs to the platform contract, not to any platform's code. Every
adapter, real or test fake, shares one latch that refuses sends and reactions
and reports degraded health while set; a facade derived from an adapter shares
its root's latch and cannot send past it. A single registry builds platforms
from one table, so start-up, the identity wait, recovery, inbound gating,
metrics and the health report run once over that table and name no platform. A
new platform inherits all of it; a platform that skips the latch fails the
contract tests.

What stays platform-specific is how an identity is resolved and which failures
mean misconfiguration rather than unavailability.

## Start-up

- **Misconfiguration refuses to start**: an unknown mode, an enabled platform
  without its credential, a bridge that refuses Nora's credential, no platform
  enabled at all. A restart cannot cure these.
- **Unavailability degrades**: a bridge that is logged out, starting,
  unreachable or answering malformed identity. It gets its bounded start-up
  wait first, so an ordinary deploy starts live.
- Nora starts with no platform live and reports unhealthy, because refusing
  would loop a crash that nothing can cure, and staying up is what lets a
  platform be promoted when it returns.

## Promotion

A degraded platform keeps re-resolving its identity, each attempt bounded and
its back-off capped. It never gives up: a logged-out bridge is re-paired by a
person at a time nobody can predict. Promotion follows one order:

1. the resolved identity is registered, joining the live set of Nora's own
   aliases;
2. the latch clears;
3. inbound opens: the listener starts, the stream reader reads and work for
   that platform is claimed.

No event is admitted before its identity is registered, so Nora can never be
mentioned by an empty or placeholder identity. The alias set is a live view,
read at each membership test and never copied; a promoted identity reaches
mention triggers, room scope and presence with no rebuild. The last known
identity is never reused, because a re-paired account can be a different one.

## Gaps and visibility

- **After an outage** each platform recovers what it can by its own means, such
  as session resume or its own history; whatever it cannot recover is a
  reported gap. A gap is observed and measured and is never indistinguishable
  from a quiet room ([observation](../cognition/observation.md)).
- Every platform's state is published as a metric on every transition, so a
  missing series means broken code, not a quiet platform.
- Health reports each platform's state. A degraded platform does not make Nora
  unhealthy; no live platform does. A deploy reads this as a warning, because
  the release did not cause it.
- Process, pairing, connection, event freshness and queue depth answer
  different questions and are never collapsed into one status.

## Why this shape

The hazard is platform-scoped: an adapter without a resolved identity is
dangerous only if it emits events, and a degraded one emits none. Promotion by
restart would let a flapping bridge restart every healthy platform. A wrapper
adapter for the latch would hide the concrete adapter that tool wiring needs.
