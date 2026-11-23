# Platform liveness: one platform's failure stays on that platform

Nora runs on several platforms at once. Each bridge is its own process so that
one transport can fail without affecting the others. Liveness keeps the app to
the same rule. An enabled platform that cannot say who Nora is goes **degraded**
alone. The process starts with the platforms that could. A degraded platform
becomes **live** again without a restart once its identity resolves.

## Terms

| state | what it means | inbound | outbound | its identity |
|---|---|---|---|---|
| `live` | the platform's own identity is resolved and registered | admitted | sends and reacts | in `identities`, and in the self-alias set |
| `degraded` | enabled, built, but its identity could not be resolved | nothing is read: no listener runs, its stream is not read, its work rows are not claimed | every send and reaction is refused, with no attempt | absent: no placeholder is ever registered |
| `disabled` | switched off in config | none | none | absent |

`nora.platform.registry.AdapterRegistry` owns the state of every platform. The
vocabulary is `nora.domain.platform_state.PlatformState`. The adapter carries
the latch (`degrade`, `promote`), so its own sends and its own `health()`
honour the state wherever they are called from.

## Startup

- Real misconfiguration still refuses to start: an unknown `mode`, an enabled
  platform with no token resolved, bridge mode with no shared bearer or no
  Redis URL, a bridge refusing Nora's credentials (401 or 403), or a Signal
  account that cannot be derived. These faults come from the deployment, so
  neither a retry nor a restart can cure them.
- A failure to *resolve an identity* (a bridge logged out, still starting,
  unreachable, or answering a malformed identity) degrades that platform
  alone. It logs one ERROR line with the reason. A bridge still gets its
  bounded startup wait first, so an ordinary deploy starts live, not
  degraded-then-promoted.
- Nora starts even when no platform is live. A restart repeats the same
  startup against the same bridges, so refusing would bring the crash loop
  back with no hope of recovery. Starting keeps the process alive to promote
  platforms when they return. `/health` then answers unhealthy, because a Nora
  nobody can reach is an outage, not a warning.
- A configuration with no platform enabled still refuses to start. Nothing
  could ever be promoted, so that case is misconfiguration, not degradation.

## Promotion

A degraded platform keeps re-resolving its identity in the background. Each
attempt is time-bounded, and the backoff between attempts is capped. It never
gives up, because a logged-out bridge is re-paired by a person, at a time
nobody can predict.

Promotion follows one order, and R-12 depends on it:

1. the resolved identity is registered, so it is in `identity_for` and the
   live self-alias set;
2. the adapter's latch is cleared;
3. inbound is opened: the listener starts, the stream reader reads, and the
   work runner claims that platform's rows.

No event from a platform is admitted before its identity is registered. A
self-tag therefore can never arrive while a placeholder or empty identity is
in place.

**The self-alias set is a live view.** `self_aliases()` is the union of the
registered identities, read at the time of each membership test. It is not a
snapshot taken at composition. Every consumer holds that view, never a copy.
A promoted identity reaches mention triggers, room scope, self-presence and
cognition with no rebuild.

## Visibility

- `nora_platform_state{platform, state}` is one-hot over `PlatformState` for
  every known platform, `disabled` included. It is published at startup and
  on every transition, so a missing series means the code is broken, not that
  the platform is quiet.
- `/health` reports each platform's `state`. A degraded platform does not make
  Nora unhealthy; zero live platforms does.
- The deploy verifier reads `/health` after the containers are healthy.
  "Nora healthy, a platform degraded" is a **warning** in the deploy log, not
  a failed deploy, because the release did not cause it.

## Why this shape

The fail-fast that came before guarded R-12 at **process** scope, but the
hazard is platform-scoped. An adapter with no resolved identity is dangerous
only if it emits events. A degraded adapter emits none and has no identity
entry, so R-12 holds without stopping the healthy platforms. Runtime failure
was already platform-scoped (one supervised task per adapter), and startup was
the last process-wide coupling.

Rejected alternatives:

- **Promote by restarting.** It is simpler, but a flapping bridge would then
  restart the whole process over and over: the coupling this design removes.
- **A wrapper adapter for the latch.** It would break the concrete-type
  narrowing composition relies on (Signal's tool wiring, Discord's music and
  presence).
- **Remembering the last-known identity.** It is a guess: a re-paired account
  can be a different number.
