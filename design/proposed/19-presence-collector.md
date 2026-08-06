---
kind: design
title: "Nora — the presence collector: our own location record, and the derivation on top of it"
description: "A separate daemon that records circle members' distinct location fixes into an encrypted store, so Nora reads a local record rather than a fragile third party — and a derivation layer that answers home/stays/patterns without ML."
tags: [nora, design, location, life360, daemon, derivation, privacy, encryption]
timestamp: '2026-08-07'
status: proposed
---

# The presence collector

Operator idea, 2026-08-07: run our own 24/7 location record, grouped by circle,
so we have a **personal location-identity service** rather than depending on
Life360 live at every read. Later, derive from it — where someone stays, where
home is, where they were before — and eventually forecast.

This is the Honcho shape applied to location: **a raw record layer and a derived
interpretation layer, the record authoritative and the interpretation always
recomputable from it.**

## 0. The name — decided first, because "location daemon" fails our own test

The operator asked for caution on naming, and rightly: "location daemon" is the
same class of failure as `resolver` (reads as DNS) and `route` (reads as BGP) —
a generic-infrastructure word that tells a reader nothing specific and collides
with other meanings. The GLOSSARY convention: **name the capability's full scope,
not the mechanism, and not another domain's word.**

- **`presence`** — the concept. Where the circle's members are and have been. Not
  "location" (a coordinate) but *presence* (a person, somewhere, over time). It
  is what the record is *of*.
- **`PresenceCollector`** — the daemon (the `<Domain><Role>` convention:
  `DoctrineStore`, `RouteCatalogue`, `BlobCipher`). It collects presence.
- **`PresenceStore`** — the record it writes.
- **`PresenceDeriver`** — the later interpretation layer (deliberately echoing
  Honcho's *deriver*, because it is the same role).
- **`whereis(person)`** — the read Nora uses, unchanged from the Life360 study.

Not `location_daemon`, not `tracker` (surveillance connotation the operator's own
dedicated-account framing is careful about), not `gps` (a sensor, not a record).

## 1. The finding that reshapes the design

The Life360 study (2026-08-07) measured that **polling buys zero freshness**: the
same member read 210s apart returned the *identical* fix, only its `age` grew.
The server returns the last fix the *phone* reported; a stationary or backgrounded
phone holds one fix for many minutes.

So the collector is **not a sampler**. Sampling every N seconds records the same
fix repeatedly — pure duplication. It is a **change-recorder**: poll at a sane
cadence, and **write a row only when `location.timestamp` advances**. That yields
a time-series of *distinct fixes*, which is exactly what derivation needs and is
a fraction of the storage.

Cadence: ~60–120 s per circle (roughly what the Life360 app itself does), token
cached, re-auth only on 403. This keeps credential POSTs rare (the flagging path)
and the request rate close to a normal client's.

## 2. Three layers, and only the first is built now

    RAW       PresenceCollector -> distinct fixes per (circle, member, ts)  -> PresenceStore
    DERIVED   PresenceDeriver   -> stay-points, home, frequent places, "before"
    PREDICT   (frequency table, maybe; a model, probably not)

**Build the collector first (§4). Defer derivation. Defer prediction hard.**
The record is authoritative and the derivation is recomputable from it, so there
is no lock-in and no rush — exactly the property that let Honcho's deriver be
tuned long after messages were being stored.

## 3. Store — SQLite is genuinely capable; do not over-engineer

Five members, distinct fixes at most every few minutes when moving and rarely
when still: thousands of rows a day, low millions a year. SQLite handles that
without strain, and it has an **R-tree** module for the one spatial query
derivation needs ("which fixes fall within radius R of a point"). No Postgres,
no TimescaleDB, no InfluxDB for a five-person circle.

Schema (raw layer):

    fixes(circle_id, person_id, ts, lat, lng, accuracy_m, is_driving,
          battery, address, wire_sharing_state, recorded_at)
    index on (circle_id, person_id, ts DESC)   -- the same shape as messages

`person_id` is Nora's canonical id (the Life360 member resolves to it through
`identity.toml`, the same mapping the roster uses), so presence and everything
else in Nora join on the same key.

**A separate database file** (`data/presence.db`), not the audit DB. Different
lifecycle, vastly different sensitivity, and it must be **encrypted at rest**
(§5). Sharing the audit DB would drag movement history into every backup and
query path that touches messages.

## 4. The collector daemon — the piece to build

A separate process, alongside signal-cli in `docker-compose.yml`, **not** part of
Nora the agent. Nora is a stateless ambient agent; this is a stateful continuous
collector. They share the `PresenceStore` and the Life360 client, nothing else.

Loop, per circle:

1. Ensure token (cached to disk 0600; re-auth only on 403).
2. `GET /v3/circles/{id}/members`.
3. For each member: if `location.timestamp` > the last stored ts for that member,
   write a fix; else skip (the dedup that makes this a change-recorder).
4. Classify the read into the study's typed enum
   (`live/stale/sharing_off/disconnected/no_fix/unavailable`) and record it, so a
   member who turned sharing off is a *known state*, not a gap.
5. Sleep the cadence.

**The canary lives here, not in Nora.** The study named the real fragility: an
app update can change the required UA/host/client-token and turn 200s into
challenge pages. The daemon runs a synthetic check (auth + one circles read) and
alarms on non-JSON or an unexpectedly empty circle, plus a counter on consecutive
`unavailable`. Nora never sees this fragility — it reads the local store.

**This inverts the earlier fragility worry in the operator's favour.** With the
collector, Nora reads a local DB (always available) and the daemon absorbs
Life360's auth/retry/breakage. If Life360 goes down, the store still holds the
last fixes with ages, and `whereis` answers 「20 分鐘前喺 Yoho」 rather than
`unavailable` — degraded, honest, and better than a live-only read.

## 5. Privacy — the largest liability in the system, named not glossed

Every other subsystem in Nora **reads and discards**. This one **persists five
people's continuous movement history**. That is a categorical change and it earns
an explicit accounting:

- **This is the most sensitive data Nora will ever hold** — more than a phone
  number, more than a photo. A breach of `presence.db` is a movement history:
  where everyone sleeps, works, and goes. The dedicated-account isolation
  protects the Life360 *credential*; it does nothing for the *recorded data*.
- **Encrypt the store at rest**, reusing the media cipher pattern
  (`BlobCipher`, AES-256-GCM, key from a host-injected env var, DEC-47). The raw
  coordinates never sit in plaintext on disk.
- **Disclosure stays R-21-shaped.** Nora may report a member's presence only into
  a conversation whose members are a subset of the circle — enforced at assembly,
  the same rule the roster uses. Today circle == group ("Ridgeline"), so this holds
  by construction; it must be *checked*, not assumed, the day a group gains
  someone outside the circle.
- **Outbound is still hard-blocked** (design/14 §2.2): a stored position may seed
  an on-host route computation but must never leave in a tool argument.
- **Staleness always reaches the model.** Position is not intent (design/14 §5.3);
  a fix's age is part of every answer, and the collector recording distinct fixes
  is what makes "when did they last actually move" answerable.

## 6. Derivation — deterministic first, and mostly not ML

When the derivation layer is built (later), the operator's three questions are
**classic algorithms, not machine learning**, and should be built as such because
they are cheap and explainable:

- **Stay-points**: cluster consecutive fixes within radius R (~150 m) held for
  duration T (~10 min). A stay-point is "they stopped here."
- **Home**: the stay-point where a member is present across the most nights.
  Deterministic, and it improves with data without a model.
- **"Where was it before"**: the previous stay-point in the sequence. A query, not
  a prediction.

**Forecasting where someone will *go* is the one genuinely uncertain part, and the
honest version is not a model** — it is a frequency table ("at this hour on this
weekday, this member is usually at X"). That gets most of the value with none of
the ML fragility, is explainable when wrong, and can be built from the same raw
store. A learned model is a much later question and should be entered sceptically.

## 7. What must be true before the collector ships

1. `presence.db` is a separate file, **encrypted at rest** with the media cipher.
2. The collector writes a fix ONLY when `ts` advances (change-recorder, not
   sampler) — probed by two reads of an unmoved member producing one row.
3. The read enum (`live/stale/...`) is recorded, so sharing-off is a known state
   not a gap.
4. The canary lives in the daemon and alarms on non-JSON / empty-circle; Nora
   reads only the local store.
5. Token cached 0600, re-auth only on 403; credential POST rate is bounded.
6. `whereis(person)` reads the store, carries the fix's age, and degrades to
   `unavailable` (never null-island) when there is no usable fix.
7. Disclosure is subset-of-circle checked at assembly (R-21 shape), and outbound
   position is hard-blocked.
8. It is a SEPARATE process in compose — it can be down without touching Nora,
   and Nora can run without it (`whereis` → `unavailable`).

Derivation (§6) and prediction are explicitly OUT of this first build.
