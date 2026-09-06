---
type: proposal
title: "The Relevance Gate — Lightweight Ambient Admission in Front of the Participation Turn"
description: "How an untagged message earns Nora's attention: one structured model call (the nora-ambient-gate combo, reasoning none, no tools, no doctrine, no agent loop) reading transcript + standing and returning verdict + relevance score. NO-GO drops. GO admits the message to the EXISTING participation occasion — the turn keeps its right to silence, which is the whole point of a second layer. Shadow mode delivers GO verdicts only to a review channel and never involves Nora. Rev 2 corrects Rev 1's central error: participation is not retired, it is what the gate stands in front of."
tags: [nora, relevance-gate, ambient, admission, lightweight, shadow, participation]
timestamp: '2026-09-06'
edges:
  - {dst: 35-ambient-participation, rel: revises, provenance: asserted}
  - {dst: 06-trigger, rel: revises, provenance: asserted}
version: 2
---

# The Relevance Gate

## 0. What Rev 1 got wrong (read this first)

Rev 1 said the participation design was "failed" and instructed its
implementation be retired. **That was the wrong cut, and it made the rest of
Rev 1 incoherent** — its own live-mode section flagged that a GO handed to
plain ambient ingestion reaches nothing (ambient constructs no agent), and its
decision list locked that handoff anyway.

The thing that failed was **using a full agent turn as the DETERMINATION** —
20-30k tokens, the whole doctrine, every tool, 3.4 seconds, and a weather tool
call on a path whose only job was to decide whether to speak. Miles's review:
«抌入去 Nora 度 run 一次».

What did **not** fail is the participation occasion itself — the machinery that
lets Nora speak in a room that did not call her, and **choose not to**. That
machinery is correct, shipped, and is exactly what must run after a GO.

**So the cut is: the gate takes over the JUDGEMENT of whether a window is
worth a turn. The participation occasion keeps the TURN.** Two layers, and the
second layer's right to silence is not redundancy — it is the design (§7).

## 1. The shape

The shipped R-1 clause 2 already reads:

> ambient traffic reaches the agent only through **the participation
> occasion's gating**

**The Relevance Gate IS that gating.** The rule does not change; what changes
is that the gating costs ~1-2k instead of ~20-30k.

```
untagged message, room opted in (relevance_gate = true in policy.toml)
  │
  ▼
quiescence window closes (settle_seconds of quiet — per-room watermark)
  │
  ▼
RELEVANCE GATE — ONE structured model call
  ├─ system prompt: short, purpose-built (a few lines, NOT Nora's doctrine)
  ├─ input: transcript window + standing. THE ONLY TWO THINGS.
  ├─ model: the `nora-ambient-gate` combo, reasoning none, no tools, no loop
  ├─ output: JSON — {"verdict": "go"|"no_go", "relevance_score": 0.0-1.0}
  │    unparseable → no_go, counted (fail-closed)
  │
  ├─ no_go → DROP. Nothing happens, nowhere. Counted only.
  │
  └─ go →
       ├─ SHADOW ON (default) → the verdict posts to the review channel.
       │    Nora is NEVER involved. No turn, no agent, no room-visible effect.
       │
       └─ SHADOW OFF → the participation occasion runs (FirePath.speak),
            original sender identity intact, relevance_score carried in as
            context. Nora may still choose silence — see §7.
```

## 2. The gate call

### 2.1 Prompt

```
You are a relevance gate for a group-chat assistant. Decide whether THIS
message deserves the assistant's attention.

Context (nothing else is available to you):
- transcript: the last N messages in the room
- standing: the assistant's own position in this conversation

Rules:
- GO only if the message is a question the assistant can answer, a fact it
  can correct, a commitment it made that is being followed up, or something
  that specifically needs it.
- NO-GO for: status updates, acknowledgements, small talk that needs no
  answer, private exchanges, inside jokes, commands for other services, and
  anything the assistant cannot add grounded value to.
- Do not suggest what the assistant would say. You only decide relevance.

Respond with JSON only:
{"verdict": "go" | "no_go", "relevance_score": 0.0}
```

### 2.2 Output contract

`verdict`: `"go"` | `"no_go"` — the admission decision, the only enforcement
surface. `relevance_score`: 0.0-1.0 — reference only: it rides into the
participation turn as context and appears in the shadow post for tuning. It is
never a threshold and never blocks (decision 5).

The gate NEVER drafts a reply, NEVER calls a tool, NEVER opens an agent loop,
NEVER sees Nora's doctrine.

### 2.3 Model + limits

- Model: the **`nora-ambient-gate`** OmniRoute combo, named in
  `[relevance_gate] model` in config.toml. A dedicated combo, not `nora` —
  the gate's requirements (JSON mode, reasoning none) differ from the tag
  lane's, and ADR 0006's measurement is the standing warning about assuming
  a combo's upstreams agree about request features.
- `reasoning_effort`: none. temperature low — this is a measurement
  instrument, not a voice.
- `max_tokens`: **to be MEASURED, not assumed** (§8 decision 7). Rev 1 wrote
  2000 by analogy. The obligation is a real measurement of the gate call's
  prompt + completion size before the number is fixed, recorded in this
  section the way `[llm] max_tokens` records its own.
- JSON mode (`response_format`) where the combo's upstreams support it; else
  strict prompt + parse, fail-closed on parse error.

### 2.4 What the gate is being bought

The comparison that justifies it, from the shipped numbers: one participation
turn's ceiling is `[llm.limits] total_tokens = 256000` over 20 turns, and the
`[llm]` prefill comment records ~29k spent "before anybody has said anything".
A gate at ~1-2k is roughly **one fifteenth to one twentieth of a turn's floor**,
and it is paid on every closed window instead of the turn being paid on every
closed window. Confirm the ratio with §8.7's measurement rather than this
estimate.

## 3. Trigger

Unchanged from the participation design and REUSED, not rebuilt: a candidate
window opens on a message and closes when the room has been quiet
`settle_seconds` (10). ONE gate call per closed window, with the whole burst in
context. Per-room watermark state — the same treatment the tag path's
`Debounce` gets, never per-message session state.

`ParticipationTrigger` is that component. It keeps its job; what changes is
that it now calls the gate first and calls `FirePath.speak` only on GO.

The send cooldown stays where it is — on the participation turn (§10.3 of the
participation design), not on the gate. The gate sends nothing.

## 4. Shadow mode

Shadow is the shipped default and the review surface.

- Every closed window runs the gate.
- **Only `go` is delivered** to the review channel: group name, incoming
  message, verdict, relevance_score (decision 6, Miles 2026-09-06).
- **`no_go` is never delivered.** It is counted
  (`nora_relevance_gate_total{verdict="no_go"}`) so the admission RATE stays
  visible even though the content is not.
- **Nora is never involved** — no turn, no agent construction, whatever the
  score.

### 4.1 Where the shadow post is allowed to live (R-1b)

`tests/architecture/test_only_two_paths_can_send.py` pins
`CALLERS = {"nora/engagement/pipeline.py", "nora/app/fire.py"}` — `deliver.build`
has exactly two caller MODULES. **A gate module calling `deliver.build` would be
a third, and R-1b goes red.**

So the shadow post goes through `nora/app/fire.py`, reusing the seam DEC-155
already established for the participation mirror (a second `deliver.build` call
site inside fire.py is blessed; a second caller MODULE is not).

**But the existing `_mirror` cannot be reused as-is**: its signature takes
`ctx`, `doctrine` and the turn's outcome (`verdict`, `texts`, `held`,
`stop_reason`) — it is built to run AFTER a turn. A shadow GO runs **no turn at
all**. A leaner sibling is required, in the same module, under its own audience
label, with the same fail-soft posture: a mirror failure is counted
(`nora_errors_total{stage="relevance_gate_shadow"}`) and never changes the
gate's verdict.

Rev 1's §9 claimed this design "does NOT touch R-1's send-path rules". That
sentence was false and is retracted: it does not CHANGE them, but it is
constrained by them, and the constraint decides where the code lives.

## 5. Live mode (shadow off, operator-authorized only)

- `no_go` → silence. Nothing is sent anywhere, nothing is constructed.
- `go` → **the participation occasion runs**: `FirePath.speak` with
  `participation_occasion(...)`, the original message's full identity intact
  (platform, conversation_id, sender_id, person_id — so the peer-card inject
  resolves the right person), and the `relevance_score` carried in as context.

**Not a synthetic tag.** A tag is must-speak; this is may-speak. The
distinction is preserved by the occasion, not by pretending nobody sent the
message.

**Carrier work required**: `participation_occasion()` currently takes only
`platform`, `conversation_id`, `conversation_type`. It needs a field for the
score, rendered into the situation header. Small, but it does not exist yet.

## 6. What the gate does NOT decide

The gate answers ONE question: **does this message deserve anyone's reply?**

It cannot answer the second question — **does Nora have something true to
add?** — because it never sees notes, facts, or the peer card. That question
is the participation turn's, and it is the reason the second layer exists.

This split is the design, not an inefficiency:

| | question | cost | sees |
|---|---|---|---|
| Gate | is this worth a reply from anyone? | ~1-2k | transcript + standing |
| Turn | do I hold anything real to add? | ~20-30k+ | everything |

## 7. The right to silence — what makes the second layer real

Miles's requirement: the gate's guess may be wrong; Nora must be able to look
and conclude «唔關我事», then say nothing. **This is already built** and is
retained unchanged. It is four mechanisms, not one:

1. **`tool_choice = auto`** on the participation runner
   (`composition.py:1266`, `tool_choice.AFTER_SPEAKING`). Under the tag path's
   `required` the model must call something on the first pass and the only
   terminal call is `send_reply` — silence is literally inexpressible.
   `tool_choice.py`'s measurements: `auto` stops 8/8 and sends 0 messages 6/6
   where `required` never stops 6/6.
2. **Recovery is occasion-gated** (`pipeline.py:879, 925, 978` — all
   `!= "participation"`). Normally prose without a `send_reply` call is
   RECOVERED into a delivery. Without this gate, "she decided not to send"
   becomes "her draft was sent anyway". This is the load-bearing one.
3. **Indicate is suppressed** — no 👀, no ✅/⚠️. A silent determination looks
   like nothing happened, instead of advertising itself to the room.
4. **R-5's matrix has a participation row** where `no_send_call` is HEALTHY,
   counted on `nora_participation_silence_total`, never in
   `nora_silent_failures_total{audience="tag"}`.

**And it is the same Nora, not a second agent.** Same model tier, same
`max_tokens`/`temperature`/`reasoning_effort`, same tools, same `identity.md`,
`voice.md`, `conduct.md`, `constraints.md`. Exactly one parameter differs:
`tool_choice`.

`participation-conduct.md` (`applies_to: [participation]`) is one extra page
that exists only for this occasion — it does not replace a persona, it adds
the rule the tag path cannot state: *"You were not called… You have the right
not to reply… Silence is a complete answer here, and a success."* It is needed
because `silence.md`'s tag-path text («you never reply to a message that didn't
call you») would otherwise instruct this turn to be permanently silent.

## 8. Decisions (locked by Miles)

1. **Name: "Relevance Gate"** (2026-09-06).
2. **JSON fail-closed** (2026-09-06): an unparseable gate response is `no_go`
   and counted. A gate that silently forwards everything is not a gate.
3. **GO admits to the PARTICIPATION OCCASION, with identity preserved**
   (REVISED 2026-09-06). Rev 1 said "handed to Nora as normal ambient
   ingestion, not a synthetic tag" — literally true of the identity, but
   ambient constructs no agent, so as written a GO reached nothing and the
   gate was inert by construction. The intent was: do not fake a tag, keep the
   original sender, and let her still choose silence. That is precisely the
   participation occasion. **This holds unless** a narrower engage path is
   built for ambient speech, at which point GO admits to that instead.
4. **Sender identity is load-bearing** (2026-09-06): platform,
   conversation_id, sender_id, person_id all preserved, or the peer-card
   inject resolves the wrong person.
5. **Score is reference only, never enforcement** (2026-09-06): it rides into
   the turn as context and shows in the shadow post. The verdict is the only
   gate.
6. **Shadow delivers GO only** (2026-09-06): `no_go` is never posted to the
   review channel. It is still COUNTED, so the admission rate is observable
   without the content.
7. **Token size is MEASURED, not assumed** (2026-09-06): the gate's
   `max_tokens` and its real prompt+completion cost are measured before the
   numbers are fixed in §2.3.
8. **Model: the `nora-ambient-gate` combo** (2026-09-06), operator-created, a
   config key in config.toml. The existing `nora` and `nora-judge` lanes are
   untouched.
9. **Settle window: 10 seconds** (2026-09-06), unchanged.
10. **Participation is NOT retired** (2026-09-06, correcting Rev 1). The
    occasion, its doctrine audience, its runner, its slot and cooldown policy,
    its metrics and its eval suite are what the gate stands in front of.

## 9. Governance — the honest scope

Rev 1 framed this as "adds an admission component and a review surface". That
under-scoped it in one direction and over-scoped it in another. Corrected:

**What does NOT change** (because participation stays):
- R-1 clause 2 and its named test — the gate IS "the participation occasion's
  gating" the rule already names.
- R-1b — two caller modules, still two (§4.1 is what keeps that true).
- R-14's `constraints.md` revision, the participation doctrine audience across
  six files, `participation-conduct.md`.
- DEC-155, the participation metrics family, the slot/cooldown policy.

**What is added**:
- `[relevance_gate]` config block: `enabled` (false), `model`
  (`nora-ambient-gate`), `shadow` (true), `shadow_channel`, `shadow_platform`,
  `max_tokens` (measured). `settle_seconds` and `cooldown_minutes` stay in
  `[participation]` — they belong to the trigger and the turn.
- `relevance_gate = true` per-room in policy.toml; `EngageDecision.relevance_gate`.
- `nora_relevance_gate_total{verdict}`, `nora_relevance_gate_errors_total`,
  `nora_errors_total{stage="relevance_gate_shadow"}`. **Never** counted in any
  tag, heartbeat or participation series.
- A leaner shadow mirror inside `fire.py` (§4.1).
- A score carrier on `participation_occasion()` (§5).
- Gate-level eval cases; the existing 15-case participation suite is retained
  for the turn behind the gate.

**What is corrected**:
- `_index.yaml` gains `36-relevance-gate.md`.
- `35-ambient-participation.md` gains a marker recording that its
  DETERMINATION design is superseded here while its speak-path machinery is
  retained. It is not wholly superseded, and marking it so would delete the
  provenance of code that is still running.

### 9.1 Deployment ordering — not optional

`nora/config.py` sets `extra="forbid"` on every model: an unknown key is a
**startup failure**. `config.toml` on master is the bind-mount source for the
running container. Adding `[relevance_gate]` to it before an image that knows
the key exists means **Nora does not come back up on her next restart**.

Order: implement on the branch (config.toml AND config.example.toml, both
`enabled = false`) → build the image → merge → deploy. The gate is enabled
nowhere by any committed config.

## 10. Rollout gates

1. Gate built, shadow-only, `enabled = false` everywhere; full suite green,
   ruff/mypy/lint-imports clean.
2. Token cost measured (§8.7) and the numbers written back into §2.3.
3. Shadow soak: `enabled = true`, `shadow = true`, rooms opted in. Miles reads
   the GO stream and the no_go rate counter.
4. The flip, on Miles's authorization only: `shadow = false`. Live sends become
   possible the moment the gate admits and the participation occasion runs —
   so this is a policy act, and no committed config performs it.
