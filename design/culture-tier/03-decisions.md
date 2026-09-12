# Decisions

**Numbers are RESERVED, not claimed.** `DEC-181 .. DEC-186`. Per AGENTS.md,
re-read `design/09-decision-index.md` at MERGE time and take the numbers then
— a number taken from a chat agreement is how DEC-131 collided in August.
Highest in use at the time of writing is DEC-180 (`ambient-loop`).

---

## DEC-181 — one key, decided before any code

`room_portrait` used `scope_id`, `note` uses `workspace`, `standing` uses
`(platform, conversation_id)`. Three tiers, three keys.

This tier introduces two scopes and therefore cannot dodge it. #202 already
cost this once: Observe knew the room, Orient overwrote it to workspace, and
semantic resolution was lost.

**OPERATOR DECISION. Blocks everything else in this bundle.** Nothing in
`01-the-tier` is buildable until it is settled, and settling it afterwards
means a migration on a tier that already has revisions.

---

## DEC-182 — the culture body is shaped, and the shape is over claim-kind

`body` is `jsonb` with exactly three sections — `stable`, `shifted`,
`standing_in_it` — each free prose under a length cap, all three required by
CHECK.

Not free-form: `standing` is what free-form costs. Not decomposed into topic
columns either: a slot table was drafted and rejected as the same enumeration
error as `participation.md`'s five shapes, and it could not have held 「秘書」.

A missing section is an **ERROR**, not an empty value. A revision that cannot
produce `standing_in_it` does not enter the store.

Three sections is a guess at what a `reasoning_effort = "none"` model can
reliably emit, informed by structured output having previously oscillated on
boundary cases. It is **UNMEASURED**.

---

## DEC-183 — provenance goes around the prose, following `note_revision`

`culture_revision` copies `note_revision`'s shape: `parent_revision_id`,
`established_by`, `instruction_sha256`, `model`, `written_at`. Plus
`support_from_id` / `support_to_id`, the message range the body was derived
from.

Being prose was never `standing`'s defect. Having no history and no source,
and being overwritten in place, is.

The support range exists for one check: **same window + same instruction hash
+ same model must re-derive a compatible body.** These cheap models were
measured at 0.20–0.50 recall on identical prompts; without this, "the room
changed" is indistinguishable from "the model felt different today".

---

## DEC-184 — Dreamer clock plus watermark, never turn-completion

`standing` fires on tag-turn completion and is consumed by the participation
path, so three of six rooms are stale by more than five days — stale in exactly
the untagged rooms ambient exists for.

Every write is a new revision, so trigger timing is low-stakes and must not be
over-tuned. Reads are fail-closed on a TTL: a head revision past its TTL
renders as **absent**, never as stale-but-present. `room_portrait` has no TTL,
which is why its 2026-08-29 row would read as current.

---

## DEC-185 — the synthesizer reads `decision_log`; the gate still does not

DEC-179 stands unmodified. `gate`, `go` and `no_go` are **not** returned to the
`decision_log` vocabulary; `test_the_trigger_records_nothing_to_a_decision_log`
keeps reddening a reintroduction.

The gate receives a folded O(1) culture block exactly as it already receives
`standing` — not a decision feed — so DEC-179's stated reason (a `note_title`
smuggling *"does Nora hold something to add?"* into admission control) is
untouched.

Reading her own past acts, joined to what the room did next, is
self-**observation**, not self-attestation: she wrote the act, the room wrote
the consequence. `PARTICIPATION_DECISION_WRITERS` excluding a decider's own
rows remains correct for the live per-window read, which is a different thing
from a slow aggregate.

Without this, `standing_in_it` cannot be written and she stays a third-party
observer of a room she is in.

---

## DEC-186 — `conversation_id` on the gate metric lands first

`nora_relevance_gate_total` carries `platform` only, so a refusal cannot be
joined to a room and neither `HOLDING refusal` nor `override` is computable.

This is a label on a metric the gate already writes. It does not touch
DEC-179, changes no behaviour, and every measurement in `02-measurement`
depends on it.

**It ships before any doctrine change.** Filed separately so it is not held
behind this bundle's approval.

---

## Not decided here

- **`participation.md` from taxonomy to contrast.** Named in
  `00-overview §2` as the eventual change, sequenced last in
  `02-measurement §7`, and deliberately not specified. Doctrine is
  `change_policy: operator-only`, and this session established that editing it
  without an instrument is what shipped the 2026-09-09 regression.
- **`silence.md`.** Widening the gate alone buys cost and no behaviour: she
  would decline for the same reason. Same sequencing rule.
- **`standing`'s repair.** Separate issue — no schema, no provenance,
  overwritten in place, and triggered off the wrong path.
- **Why `proactive_decide` declined 7/7** while holding the notes it woke on.
  A second, independent fault; likely the `memory before remind` path from
  #138.
- **Dropping `room_portrait`, `room_lexeme`, `room_exchange`.** Per the
  2026-09-07 rule the tables stay when the code goes; dropping them is its own
  explicit decision.
- **Phase 2.** `04-phase-two-note.md`, not executable.
