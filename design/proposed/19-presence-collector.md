---
kind: design
title: "Nora — the presence collector: our own location record, and the derivation on top of it"
description: "A separate daemon that records circle members' distinct location fixes into the `nora` database — PLAINTEXT, in the same store as the record — so Nora reads a local record rather than a fragile third party, with a derivation layer that answers home/stays/patterns without ML."
tags: [nora, design, location, life360, daemon, derivation, privacy, presence]
timestamp: '2026-08-07'
status: proposed
---

# The presence collector

> Content-policy update, 2026-10-25: phone/UUID/coordinate, place/person-name
> and location audience filters described here remain implemented and tested in
> enabled mode, but runtime defaults them off through `[content_checks]`.
> Credentials, doctrine, scope, consent and availability are independent.
> See the [current decision](../../docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md).


Operator idea, 2026-08-07: run our own 24/7 location record, grouped by circle,
so we have a **personal presence service** rather than depending on Life360 live
at every read. Later, derive from it — where someone stays, where home is, where
they were before — and eventually forecast.

This is the Honcho shape applied to location: **a raw record layer and a derived
interpretation layer, the record authoritative and the interpretation always
recomputable from it.**

**This revision exists because two things in the first draft were wrong, both
corrected by the operator (2026-08-07):**

1. **The store is not a separate encrypted SQLite file.** Presence lives in the
   **same `nora` Postgres database** (design/20), as an ordinary table. The
   "separate `presence.db` with an R-tree, `BlobCipher`-encrypted" design is
   withdrawn — it predates the Postgres decision and its encryption story was
   incoherent for a queryable store (you cannot index bytes you have encrypted).
2. **Presence is PLAINTEXT.** The operator was explicit: presence does not require
   encryption; only *media bytes* are encrypted (§5 explains the reasoning and
   names the residual liability honestly rather than pretending it away).

## 0. The name — decided first, because "location daemon" fails our own test

The operator asked for caution on naming, and rightly: "location daemon" is the
same class of failure as `resolver` (reads as DNS) and `route` (reads as BGP) — a
generic-infrastructure word that tells a reader nothing specific. The GLOSSARY
convention: **name the capability's full scope, not the mechanism, not another
domain's word.**

- **`presence`** — the concept. Where the circle's members are and have been. Not
  "location" (a coordinate) but *presence* (a person, somewhere, over time).
- **`PresenceCollector`** — the daemon (the `<Domain><Role>` convention:
  `DoctrineStore`, `RouteCatalogue`, `BlobCipher`). It collects presence.
- **`PresenceStore`** — the read/write seam over the `presence_fixes` table.
- **`PresenceDeriver`** — the later interpretation layer (deliberately echoing
  Honcho's *deriver*, because it is the same role).
- **`whereis(person)`** — the read Nora uses, unchanged from the Life360 study.

Not `location_daemon`, not `tracker` (surveillance connotation the operator's own
dedicated-account framing is careful about), not `gps` (a sensor, not a record).

## 1. The finding that reshapes the design

The Life360 study (2026-08-07) measured that **polling buys zero freshness**: the
same member read 210 s apart returned the *identical* fix, only its `age` grew.
The server returns the last fix the *phone* reported; a stationary or backgrounded
phone holds one fix for many minutes.

So the collector is **not a sampler**. Sampling every N seconds records the same
fix repeatedly — pure duplication. It is a **change-recorder**: poll at a sane
cadence, and **write a row only when `location.timestamp` advances past the last
stored `ts` for that member** (a `since`-style dedup — one indexed read of the
member's latest `ts`, compare, insert-or-skip). That yields a time-series of
*distinct fixes* — exactly what derivation needs, at a fraction of the storage.

Cadence: ~60–120 s per circle, in config.toml, token cached, re-auth only on 403.
This keeps credential POSTs rare (the flagging path) and the request rate close to
a normal client's.

## 2. Three layers, and only the first is built now

    RAW       PresenceCollector -> distinct fixes per (circle, member, ts) -> presence_fixes
    DERIVED   PresenceDeriver   -> stay-points, home, frequent places, "before"
    PREDICT   (frequency table, maybe; a model, probably not)

**Build the collector first (§4). Defer derivation. Defer prediction hard.** The
record is authoritative and the derivation recomputable from it, so there is no
lock-in and no rush — the property that let Honcho's deriver be tuned long after
messages were already being stored.

## 3. Store — one table in the `nora` database, plaintext, no spatial index

Presence is a table in the **same `nora` Postgres database** the record store
uses (design/20). It relates to everything else by `person_id` — the same
canonical id the roster uses — so presence, messages, and identity **join
natively, in one database**, which is the entire reason the store was unified.

    presence_fixes(
      circle_id, person_id, ts, lat, lng, accuracy_m, is_driving,
      battery, address, wire_state, recorded_at
    )
    index on (circle_id, person_id, ts DESC)   -- same shape as messages;
                                                --   ts is BIGINT (design/20 §2)

**The queryable keys are `circle_id` / `person_id` / `ts` — plaintext, indexed.**
Every query presence needs is *"this member's fixes over this time window"* —
keyed on those, never on coordinates. `whereis` is the latest row for a person;
derivation reads a person's window and clusters app-side. So there is **no spatial
query against the database and no need for PostGIS or an R-tree**: at five members,
"which fixes are within radius R" is an app-side distance loop over a decrypted-—
no, *read* — time window, cheap and explainable. (The R-tree was a solution to a
query the design does not make.)

`ts` is `BIGINT`, not INT4 — the same overflow fix design/20 §2 makes for
messages. Life360 fix timestamps are epoch milliseconds; INT4 would crash the same
way, silently, under R-5.

## 4. The collector daemon — the piece to build

A **separate process and a separate container** in `docker-compose.yml`, **not**
part of Nora the agent (`command: python -m nora.presence`, one domain + one
infra, two `__main__`s — the DDD/CA shape design/20 §3 lays the substrate for).
Nora is a stateless ambient agent; this is a stateful continuous collector. They
share the `nora` database and the Life360 client library, nothing else, and either
can be down without the other: if the collector is down, `whereis` degrades to
`unavailable`; if Nora is down, the collector keeps recording.

Loop, per circle:

1. Ensure token (cached to disk 0600; re-auth only on 403). **The 403 path is
   guarded against a re-auth loop** — a 403 that a *fresh* token also gets is a
   ban signal, not a stale-token signal: back off exponentially and alarm, never
   hammer `token.json` in a tight loop (the study named this as the ban risk).
2. `GET /v3/circles/{id}/members`.
3. For each member: if `location.timestamp` > the last stored `ts` for that
   member, write a fix; else skip (the `since`-based dedup that makes this a
   change-recorder, §1).
4. Classify the read into the study's typed enum
   (`live/stale/sharing_off/disconnected/no_fix/unavailable`) and record
   `wire_state`, so a member who turned sharing off is a *known state*, not a gap.
5. Sleep the cadence.

**Liveness lives in the daemon, not in Nora.** The study named the real fragility:
an app update can change the required UA/host/client-token and turn 200s into
challenge pages. The daemon runs a synthetic check (auth + one circles read),
publishes a **heartbeat / last-successful-collection age** on its own metrics
endpoint, and alarms on non-JSON, an unexpectedly empty circle, or a rising count
of consecutive `unavailable`. Nora never sees this fragility — she reads the local
table.

**This inverts the earlier fragility worry in the operator's favour.** With the
collector, Nora reads a local DB (always available) and the daemon absorbs
Life360's auth/retry/breakage. If Life360 goes down, the table still holds the
last fixes with ages, and `whereis` answers 「20 分鐘前喺 Yoho」 rather than
`unavailable` — degraded, honest, better than a live-only read.

**Retention: 180 days, in config.toml** (operator decision). A periodic prune
deletes fixes older than the window — the same *periodic sweep, never on the write
path* shape the record store's `prune_all` uses, so a retention chore never
contends with a collection write. `ts` in milliseconds; the cutoff is computed,
not hard-coded.

## 5. Privacy — plaintext by decision, liability named not glossed

Every other subsystem in Nora **reads and discards**. This one **persists five
people's continuous movement history.** That is a categorical change, and the
review discipline (R-5, and CLAUDE.md's "name the liability") requires it be
accounted for honestly — *especially* because the decision is to store it
plaintext.

- **The data is PLAINTEXT in the `nora` database** (operator decision,
  2026-08-07). Unlike media *bytes* — write-once blobs encrypted with `BlobCipher`
  (DEC-47) — presence is a queried relation: encrypting the coordinates would
  defeat the time-window reads that are its whole purpose, and the operator judged
  encryption unwarranted for a five-person, self-hosted circle. This spec records
  that decision *and* does not pretend the sensitivity vanished with the
  ciphertext.
- **The residual liability, stated plainly:** `presence_fixes` is the most
  sensitive data Nora holds — more than a phone number, more than a photo. A
  breach of the `nora` database is a movement history: where everyone sleeps,
  works, and goes. The dedicated Life360 account isolates the *credential*; it
  does nothing for the *recorded data*.
- **The compensating controls that make plaintext acceptable at this scale:**
    - the `nora` database **publishes no host port** and is reachable only on the
      private compose network (design/20 §3) — it is not exposed beyond the
      containers that need it;
    - **disclosure is subset-of-circle — but the seam does not exist yet, and
      this spec must not pretend it does** (both reviews, HIGH). The existing R-21
      machinery is *resolve-within-scope* — a `RoomView` that is a subset of the
      real room, so Nora can't *name someone not in the room*. Presence needs the
      **inverse**: refuse to *disclose about a circle member into a room that
      contains someone outside the circle*. Nothing in R-21 compares a Life360
      circle to a Signal group; "the same rule the roster uses" was an
      overstatement. What is actually buildable, and what `whereis` must carry: at
      answer assembly, intersect the conversation's `RoomView.people` with the
      collector's stored circle roster and **refuse (not omit)** on any member
      outside the circle, plus a **startup invariant that alarms the day group ⊄
      circle**. Today circle == group ("Ridgeline") so it holds by construction —
      *verified at startup*, not assumed;
    - **outbound stays blocked except where the room itself is trusted**
      (design/14 §2.2, amended by R-57): a stored position may always seed an
      on-host route computation, and may leave in a `route` tool argument ONLY
      when `Conversation.location` is true for that room — the same flag
      `where_is`/`saved_places` already answer to, so this adds no new trust
      surface, it reuses the one the operator already set per room;
    - **staleness always reaches the model** (design/14 §5.3): position is not
      intent, a fix's age is part of every answer, and recording *distinct* fixes
      is what makes "when did they last actually move" answerable.
  If the operator later wants defence-in-depth for the powered-off disk, that is
  disk-level (LUKS) on the host volume — orthogonal to this design and reversible;
  it is deliberately not in this build.

## 6. Derivation — deterministic first, and mostly not ML

When the derivation layer is built (later), the operator's three questions are
**classic algorithms, not machine learning**, and should be built as such because
they are cheap and explainable:

- **Stay-points**: cluster consecutive fixes within radius R (~150 m) held for
  duration T (~10 min). A stay-point is "they stopped here." App-side distance,
  no spatial index (§3).
- **Home**: the stay-point where a member is present across the most nights.
  Deterministic, improves with data without a model.
- **"Where was it before"**: the previous stay-point in the sequence. A query, not
  a prediction.

**Forecasting where someone will *go* is the one genuinely uncertain part, and the
honest version is not a model** — it is a frequency table ("at this hour on this
weekday, this member is usually at X"). Most of the value, none of the ML
fragility, explainable when wrong, built from the same raw table. A learned model
is a much later question, entered sceptically.

## 7. What must be true before the collector ships

1. `presence_fixes` is a table in the `nora` database (design/20), **plaintext**,
   `ts` is `BIGINT`, indexed `(circle_id, person_id, ts DESC)`.
2. The collector writes a fix ONLY when `ts` advances (change-recorder, not
   sampler) — probed by two reads of an unmoved member producing one row.
3. The read enum is recorded as `wire_state`, so sharing-off is a known state, not
   a gap.
4. The 403 path backs off and alarms rather than re-auth-looping; credential POST
   rate is bounded; token cached 0600, re-auth only on 403.
5. Liveness (heartbeat + last-collection age) and the canary live in the daemon
   and alarm on non-JSON / empty-circle; Nora reads only the local table.
6. `whereis(person)` reads the table, carries the fix's age, and degrades to
   `unavailable` (never null-island) when there is no usable fix.
7. Disclosure: `whereis` assembly intersects the conversation's members with the
   circle roster and **refuses (not omits)** on any member outside the circle; a
   startup invariant alarms the day group ⊄ circle. Named as a new seam, not a
   claim of existing R-21 enforcement (both reviews). Outbound position stays
   hard-blocked (design/14 §2.2).
8. `presence_fixes` joins the same Alembic chain in the `nora` DB; its
   `(circle_id, person_id, ts DESC)` index is **hand-written and
   autogenerate-excluded** (the alembic-check-blind DESC shape, CLAUDE.md), and
   the rebuilt parity gate (design/20 §2) covers it — CHECK/DESC assertions, drift
   turns red.
9. Retention is 180 days from config.toml, pruned by a periodic sweep off the
   write path.
10. It is a SEPARATE container/process in compose — down without touching Nora,
    and Nora runs without it (`whereis` → `unavailable`).

Derivation (§6) and prediction are explicitly OUT of this first build.

## 8. Decision-index entries this creates

- **DEC-54** — presence is a plaintext table in the `nora` database, not a
  separate encrypted store; media bytes remain the only encrypted-at-rest data.
  Enforced by criteria 1 and the §5 disclosure/outbound controls.
- **DEC-55** — the collector is a change-recorder (write on `ts` advance), a
  separate container, with 403 back-off and in-daemon liveness. Enforced by
  criteria 2, 4, 5, 9.
- The **RULE test** for each lands in `tests/test_acceptance_rules.py::RULE_TESTS`.
