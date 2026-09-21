# 11 — The sandbox rebuild: one Nora, rebuilt from production, run isolated

**Status: §1.2's rule and §7.1's three states are BUILT (2026-09-21, see
§1.3). §2–§6's replay/rebuild machinery is not.**

## 0. The rule

> **Every case runs a COMPLETE Nora, rebuilt from production data by
> production's own code, into isolated storage. No tier is missing, no tier is
> authored, and no case is assembled differently from any other.**

One rule, no exceptions, nothing per-case. If a tier cannot be rebuilt, the case
does not run — it is never quietly assembled without it.

**The rule is not an optimisation to be justified case by case. It is the only
defensible default, because the thing that would license any exception cannot be
measured.** See §1.1.

## 1. What this replaces, and why twice was not enough

Two earlier drafts of this document both failed the same way, and the failure is
worth recording because it is the suite's own historical disease.

- **Draft 1** proposed a per-case `requires:` listing which tiers a case depends
  on, filling only those. It was justified by a count: 24 of 150 cases declare a
  `standing:`, 126 do not.
- **Draft 2** kept one assembly but accepted Honcho as "a uniform divergence,
  stated rather than hidden."

Both are the same mistake at different sizes: deciding, per tier or per suite,
that something production ALWAYS supplies may be absent here. A case assembled
without standing is not a case that does not need standing — it is a case
running a **different prefill than production**, measuring a different Nora,
with the difference invisible in the report. That is exactly what the rebuild
exists to end: the previous suite's cases were each patched together their own
way and collectively measured nothing coherent.

Draft 2's Honcho concession was also simply **wrong on the facts** — see §3.

But the deeper error is Draft 1's, and it is not that the count was wrong. The
count is accurate and it is **not evidence of anything about sensitivity**.

## 1.1 Why sensitivity cannot be measured, and what follows

"24 of 150 declare a `standing:`" measures what the cases' AUTHORS WROTE. It does
not measure what changes the output. Those are different propositions, and no
experiment available here converts one into the other:

- **The negative is unfalsifiable.** Running the suite with a block present and
  absent and observing no difference establishes nothing. It is n samples of a
  STOCHASTIC process, on the m inputs that exist today, under one model, one
  doctrine and one instruction revision. Absence of observed effect is not
  absence of effect. (This suite has already measured its own noise floor: an
  identical `proactive_decide` prompt scored 0.20–0.50 recall at n=5, and a
  40-case gate suite scored 0 to 2 errors run to run on an unchanged build.)
- **The scope is open, not closed.** Even a sound result for today's 150 cases
  says nothing about the 151st, about a new input, or about the same input after
  a doctrine change. A subtraction justified once would have to be re-justified
  on every change to anything — which is the same as never being justified.
- **The judgement is semantic.** Whether the room's standing changes how a line
  should be read is not arithmetic. It is the model reading a whole context.
  Every block is part of that reading by construction.

So the asymmetry is total: **adding a tier that turns out not to matter costs a
model call. Omitting a tier that does matter silently produces a number that
looks exactly like a valid one.** One is a bill; the other is a false reading
that nothing downstream can detect.

This is why R1 (§6) is unconditional rather than evidence-gated. There is no
evidence that could gate it. A rule requiring proof that cannot exist is a rule
with no exceptions, which is the only kind that cannot drift.

**Consequence for this document's own history:** every sensitivity count
produced during its drafting — "24 standing-sensitive", "0 culture-sensitive",
"0 of the 54 converted cases are standing-sensitive" — is hereby retired as a
basis for any decision. The last of those was used to retract a warning that the
54 cases converted on 2026-09-21 assemble without standing. **The retraction was
wrong and the warning stands** (§7).

## 1.2 The subject itself diverges, and that is upstream of every tier

Seeding the right context into the wrong Nora measures nothing. Measured
2026-09-21, the eval subject is not the production subject:

**Config.** `nora/subject/obtain.py:92` builds every subject from
`config.example.toml` — the repo's EXAMPLE — not from the deployment's
`config.toml`. **65 values differ.** Not trivia:

| | example (what every eval ran) | production |
|---|---|---|
| `llm.model` | `ollamacloud/deepseek-v4-flash:0731` | `nora` |
| `llm.reasoning_effort` | `low` | `none` |
| `relevance_gate.enabled` / `.shadow` | **False** / **True** | True / False |
| `participation.enabled` | **False** | True |
| `standing.enabled` | **False** | True |
| `culture.enabled` | **False** | True |
| `notes.enabled` | **False** | True |
| `dreamer.enabled` / `.model` / `.reasoning_effort` | **False** / flash / `none` | True / `nora-dreamer` / `low` |
| `search` `online` `weather` `route` `preview` | **all False** | all True |
| `doctrine.canary` | `CHANGE-ME-PER-DEPLOYMENT` | the real canary |

So every case to date evaluated a DIFFERENT MODEL at a DIFFERENT REASONING
EFFORT, with the relevance gate off and shadowed, with participation, standing,
culture, notes and the Dreamer disabled, and with most tools switched off.

This also explains a finding blamed on the entry point: `RelevanceGatePolicy` is
never wired not only because `entry: tag` omits the flag, but because
`relevance_gate.enabled` is False for the whole suite.

**Policy.** `default_policy()` emits one room with `persons`/`unit` and NO
per-room flags. Production's `policy.toml` carries 6 conversations with
`relevance_gate = true` on 3, `home_room = true` on 3, `voice = true` on 2. So
even with the gate enabled in config, the isolated room is not opted in.

**The per-case `config:` overrides are the symptom.** 22 cases set
`notes.enabled` and 8 corpora set `engagement.trigger.text` — cases compensating
by hand for a baseline that was wrong underneath them. Under the rule below they
all disappear.

### The rule

> The subject composes **the deployment's own config and the real room's own
> policy entry**. The ONLY permitted differences are the isolation keys: the
> room id, and the unit/workspace. Nothing else is settable, per case or
> per corpus.

An example file is by definition not production and drifts from it by design, so
it cannot be the baseline. The config is COPIED from the deployment into the
sandbox workdir per run, and the run records its digest — so what a number was
produced under is re-derivable rather than assumed.

**Consequence to settle (operator).** Production has
`engagement.trigger.enabled = false` — the text trigger was disabled
2026-09-01. `trigger.yaml` (12), `no_trigger.yaml` (12) and `near_miss.yaml` (4)
assert the behaviour of a mechanism production no longer runs. Under this rule
they do not merely change config; they test a path that does not exist in the
deployment. What those 28 are for is a question about the corpus, not about the
harness.

### 1.3 What landed 2026-09-21 — one path, isolated by database

**Built.** `evals.backbone._drive` has ONE path. Every case names a real room
and a real instant (`snapshot: {platform, conversation_id, before_ms}`) and
nothing else about what she knows. There is no `knows:`, no `config:`, no
`policy:`, no `room:`, no `now:`; `evals.scenarios.load_all` refuses them by
name. The three states of §7.1 are live: `census()` counts active / parked /
unanchored per corpus and `python -m evals` prints them beside every result.

**Isolation is by DATABASE, not by room.** The previous design shared one
database across cases and minted a hashed room per case (`room_for`,
`workspace_for`, `sandbox_room_for`). That is gone. Each case composes
against a sandbox database of its own, cloned from a blank migrated template
BESIDE the source (`nora.subject.sandbox.sandbox_database`, the same
`CREATE DATABASE … TEMPLATE` the test suite already used per test), and is
dropped when the case ends. So:

- the room is the REAL room's own id, and the deployment's `policy.toml` and
  `identity.toml` are copied VERBATIM — no swapped entry, no Discord-shaped
  minted id, no one-person roster;
- the unit and the culture key are RESOLVED from the room's policy entry at
  `obtain()` (`Subject.unit`, `Subject.culture_key`) and are not a case's to
  state. An earlier check that `unit == memory.workspace_id` is deleted: the
  deployment itself fails it on every room, because `memory.workspace_id` is
  Honcho's per-deployment workspace and a unit is per room;
- the subject's clock is frozen AT `before_ms` and every seeded row keeps its
  real `ts`. Nothing is shifted. The clock is a port (`01-boundaries` §3), so
  the shift the earlier sandbox needed is no longer needed;
- **the source is the deployment's clone**, `nora_eval` on the deploy host
  (`scripts/refresh_eval_clone.sh`, `make eval-source`), opened read-only.
  Production is never opened by the suite, and `python -m evals` refuses a
  source whose name is not a clone's (`*_eval`).

**Honcho** is the one store a database does not isolate. `_baseline()` points
`memory.workspace_id` at a fixed `<deployment>-eval` sibling — stable, not
per case, so §3's replay can one day fill it. Until then every sandbox's peer
card is EMPTY. That is a stated tier gap, not a hidden one.

**What a case may still say about its input.** `input` is the line that
arrives at `before_ms`; `person` is who says it (a `person_id` the roster
knows — the room's `persons` gate would otherwise refuse the line silently);
`mention: true` marks it as @-mentioning her, structurally
(`NormalizedMessage.mentions`). With the text trigger off since 2026-09-01,
a mention IS how the inbound path engages her, so every `tag` case expecting
a reply states one. Discord's adapter is off in the sandbox, so those cases
anchor to Signal rooms.

**Decided here, where the operator question above was open.** The
trigger/no_trigger/near_miss cases keep their inputs (a trigger-shaped line
is still what arrived); the reply-expecting ones engage her by mention.
What they measure is therefore "a mention with this text gets a grounded
reply" and "trigger-shaped text with no mention gets silence", both of which
the deployment does run.

**Still empty in the sandbox** (the extraction is `snapshot.py`'s four tiers):
reminders, note candidates, debounce/window state, Honcho. A `proactive_act`
case, which fires a due reminder row, has no anchor until reminders join the
extraction; those cases are parked P-A with that return condition. R1 is not
yet enforced mechanically — a case whose tier is empty runs — because the
extraction cannot yet tell "empty then" from "not extracted"; the counts in
this section are what to read until it can.

### Where the suite runs — DECIDED 2026-09-21: on the deploy host

Not a preference. Measured: with the deployment's features on, composition
**refuses to start** outside its environment.

| blocker | why it cannot be faked |
|---|---|
| `NORA_DATA_HOST_ROOT` | a HOST-side docker bind-mount path. `[compute]` refuses to start without it, because an in-container path "silently resolves to an empty host directory" |
| Discord `GET /users/@me` | the adapter authenticates for real at startup; a placeholder token is HTTP 401 |
| signal-cli `127.0.0.1:18080` | unreachable off-host; self-id silently falls back |
| Honcho `host.docker.internal:8000` | same — every peer write fails |

Beyond startup, a long tail DEGRADES rather than fails: `route`, `search`,
`online`, `image_gen` and `shortener` each warn "enabled but $KEY is unset" and
carry on with the tool unavailable. **That degradation is a divergence**:
production's Nora holds `web_search`; a subject composed without its key does
not, and a case whose turn would have reached one measures a Nora missing it.

The mechanism is `os.environ.setdefault` in `_baseline()`, and it is ONE
composition path in two environments rather than two paths: on the deploy host
the real values win and the tools are production's; anywhere else placeholders
fill in, composition still succeeds, and the tools degrade. The repo's own
integration tests keep working on a laptop for exactly this reason — they test
the harness rather than evaluate Nora.

`NORA_DATA_HOST_ROOT` is the interesting one: outside a container there is no
mount, so the host path IS the local path. The workdir is not a placeholder
there — it is the correct answer.

**A run states where it ran.** `python -m evals --deployment-config` defaults to
the deployment's own `config.toml`; `materialise()` rebuilds
`config.evaluation.toml` from it before any case and REFUSES when the two
disagree outside `ALLOWED_TO_DIFFER`. A missing deployment config stops the run
rather than silently falling back to the example.

### A declared divergence that is not yet closed

**Discord is disabled in the sandbox.** Signal's adapter is neutralised by a
placeholder account and stays enabled as the deployment has it; Discord's cannot
be, because it resolves its own identity with a live `GET /users/@me`.

The cost is real and is stated rather than hidden: the registry's
`self_aliases()` feeds `self_ids`, which `MentionPolicy`/`ReplyToNoraPolicy`
match on. With Discord off, a Discord room's mention-and-reply engagement cannot
fire — and since the text trigger was disabled 2026-09-01, mention-and-reply IS
the deployment's engagement path. No corpus exercises it today, so nothing is
measurably wrong yet. A mention/reply corpus on Discord is blocked on closing
this.

## 2. Every tier has a source. There are no exceptions.

A tier is either RECOVERED (history recorded it) or REPLAYED (production's own
producer regenerates it from the recovered transcript). Nothing is authored.

| tier | mode | source |
|---|---|---|
| transcript | recovered | `messages` (2026-08-05→) |
| notes | recovered | `note` + `note_revision` (2026-09-07→) |
| standing | **replayed** | `standing_event.Wiring` — `composition.py:1815` |
| culture | **replayed** | `CultureAdaptor` — `composition.py:3208` |
| peer card / memory | **replayed** | `MemoryStore.feed()` — `ingest/stages/memory_feed.py:47` |

Recovery has a floor: `standing_revision` begins 2026-09-20 14:13 (4 rows, one
room) because `0059` deliberately did not backfill, and the pre-0059 schema held
only the current value — there is nothing to backfill FROM. `culture_revision`
begins 2026-09-17. Those floors are why standing and culture are replayed rather
than recovered, and replay has no floor: it needs only the transcript, which
goes back to 2026-08-05.

## 3. Honcho is replayed, not conceded

Draft 2 called Honcho unrecoverable because it holds no point-in-time history
and its representations drift. The first half is true and the conclusion does not
follow: **you do not snapshot Honcho, you replay it**, exactly as with standing.

Production's own write path is one ingest stage:

```python
await memory.feed(peer_id=..., text=..., conversation_id=..., metadata={"ts": ts})
```

Every field it needs is on a recovered transcript row — `person_id` → peer,
`body` → text, the room, and `ts`. The read path back into the prompt is
`peer_card()` (`prefill.py:538`). So a rebuild feeds the real transcript through
the real stage into the isolated workspace and Honcho derives its own peer cards
from it — the same producer on the same data.

A representation cannot be written directly, only derived. That does not matter,
because of §4: the rebuilt WORKSPACE is the frozen artifact, not a file
extracted from it.

## 4. The rebuild is the artifact: pre-built, content-addressed, frozen

The unit is not a per-tier file. It is **one rebuilt workspace per anchor**,
which every case at that anchor shares.

- **Pre-built.** Constructed ahead of any run, never lazily during one. Replay
  costs model calls (the fold) and derivation time (Honcho); paying it per run
  makes a case's input vary per run, and the suite measures noise instead of
  behaviour — the `proactive_decide` recall-at-n=5 finding.
- **Content-addressed.** Keyed by the anchor, so sharing is by construction
  rather than coordination.
- **Frozen.** Built once, reused thereafter. A changed objective or model does
  not silently rebuild; a human moves it.

```
anchor-key = sha256(platform | conversation_id | before_ms | folded_to_ms)[:16]
```

```
evals/corpora/rebuilds/
  _index.yaml         # anchor-key -> room, instants, built_at, digests, provenance
```

The index row carries what makes the rebuild re-derivable and falsifiable:

```yaml
anchor_key: <sha256[:16]>
platform: signal
conversation_id: "<real room>"
before_ms: <T2>                 # the transcript window
folded_to_ms: <T1>              # T1 <= T2; where the folds were replayed to
transcript_digest: "<sha256 of the rows replayed>"
producers:
  standing: {agent: standing.event_fold, model: "...", instruction_sha256: "...",
             fold_batch: <n>, rollup_max_folds: <n>, fold_count: <n>}
  culture:  {agent: "...", model: "...", instruction_sha256: "..."}
  memory:   {peers: <n>, fed: <n>}
built_at: "<ISO-8601>"
```

`transcript_digest` is the falsifiable binding: re-read the same rows and get the
same digest, or the anchor no longer denotes what was built.

Cost scales with **distinct anchors, not cases** — one agent already reused 3
anchors across 33 cases.

## 5. The one knob: staleness is a timestamp

`before_ms` is the transcript window. `folded_to_ms` is where the folds were
replayed to, and `T1 <= T2`.

`T1 == T2` (the default) gives standing current with the room. `T1 < T2` gives
standing that lags it — not exotic: production folds every `fold_batch`
messages, so standing ALWAYS trails the newest lines. That is what
`a10_the_transcript_outranks_her_own_summary` is about, and it is expressed by
choosing T1, not by hand-writing a stale summary.

This is a property of the anchor. It is not a per-case choice about which tiers
to inject, and it is the only dial a case turns.

## 6. Refusals (fail-closed)

Every one is an ERROR. An ERROR is never a PASS.

| # | condition | why |
|---|---|---|
| R1 | a case assembles with ANY tier empty | the rule: no case runs a reduced prefill |
| R2 | `folded_to_ms` > `before_ms` | folded from messages the room had not yet said |
| R3 | a rebuild's `transcript_digest` ≠ the rows read now | the anchor no longer denotes what was built |
| R4 | a referenced anchor-key is absent from `_index.yaml` | a typo reads as an empty tier |
| R5 | any producer in `producers:` is missing its model or instruction sha | unprovenanced, so not re-derivable |
| R6 | a replay reports fewer peers fed than the transcript has speakers | a silently partial memory rebuild |
| R7 | a case states any window, limit or cap of its own | a tier PRESENT but REDUCED is the same divergence as one absent |

**R7 names a defect already in the tree.** `snapshot:` as shipped on
2026-09-21 accepts a per-case `transcript_limit:`. Production does not choose a
window per message — it reads one from config per entry point
(`cfg.relevance_gate.transcript_rows`, `composition.py:2871`). A case setting its
own is choosing an assembly production never chooses, which is R1's disease
expressed as a number rather than an absence. The knob must be removed from the
case surface; the window is whatever the composed config says for that entry
point.

This generalises: **completeness is not only about which tiers are present, but
about every tier being assembled the way production assembles it.** Any future
per-case knob over assembly is the same defect wearing a new name.

**R1 is the hinge**, and it needs no per-case declaration precisely because it is
unconditional — which is what makes quiet divergence impossible. It turns
"assembled a stripped room and passed" into "refused to run."

## 7. What this costs, stated honestly

- **All 150 cases need a rebuild**, not 24.
- The 54 cases converted 2026-09-21 currently assemble without standing, and most
  without culture or memory. Under R1 they refuse to run until rebuilt. That is
  the correct verdict, not a regression.
- A rebuild costs the fold's model calls plus Honcho derivation latency, once per
  distinct anchor.
- Hand-authored cases cannot satisfy R1: nobody hand-writes a realistic standing,
  culture and peer card per case. **R1 therefore forces every case onto an
  anchor.** A case whose scenario never occurred in a real room has no anchor,
  and is not convertible — it is deleted or it waits for the room to produce the
  moment. There is no third option that preserves the rule.

## 7.1 Parking (operator decision, 2026-09-21)

R1 forces every case onto a real anchor. A case with no anchor cannot run. It is
PARKED rather than deleted — removed from the active set, keeping its full
content, carrying a reason and the condition that would bring it back.

**Three states, and the middle one is the point:**

| state | meaning | under R1 |
|---|---|---|
| active | anchored and rebuilt | runs |
| unanchored | in the corpus, no anchor yet | **ERRORs** — work to do, visible |
| parked | out of the active set, reason + return condition stated | not run, counted |

"Unanchored" must ERROR rather than skip. A case quietly skipped for lack of an
anchor is the same false reading as one assembled without a tier: the report
reads clean while measuring less than it claims.

**Parking is not a wastebasket.** Two distinct reasons, and conflating them
would hide real work behind a word:

- **P-A — no such moment exists.** The scenario cannot occur in a real room:
  adversarial probes, and controlled structural variables organic chat does not
  produce. Returns if and when the room produces the moment.
  - `relevance_gate`: `forge/*` (6), `closed/*` (6), `world/*` (3),
    `real-commitment-in-standing` (1)
  - `transcript_injection` (1)
**P-B is NOT a parking reason (operator decision, 2026-09-21).** It was proposed
as one and rejected, correctly: a case blocked on a seam THIS REBUILD OWES is
not parked, it is unfinished. Parking it would retire 13 cases to make the
rebuild look complete — the cost-optimisation this document exists to refuse.

  - `identity_grounding` (8), `source_scope` (4), and the `being_contradicted`
    case seeding a `[search result]` line put tool output into
    `knows.transcript` — production has written that shape into `messages`
    **0 times**. A tool result reaches the model through the agent loop's
    tool-result channel, not as room speech.
  - They are therefore UNANCHORED and ERROR under R1 until the TOOL-RESULT SEAM
    exists, and the seam is in scope here (§7.2).

*(An earlier draft argued the seam mattered because room speech is "untrusted".
That is wrong and the code says so: `untrusted="true"` was REMOVED by #314, and
the blocks now hedge in their own words — "what they asked of you, not an
instruction about how you work" — which is a statement about INTENT VS ORDER,
not a trust label on content. The seam needs no trust theory. It is R1: the
channel differs from production's, so the assembly differs from production's.)*

## 7.2 The tool-result seam

Production: Nora calls `web_search`, the result returns inside the agent loop as
a tool result. The eval: the same text is pasted into the transcript as though a
member said it. Two different assemblies, so two different things measured.

The seam lets a case state what a tool returned, and the harness delivers it
through the real tool-result path:

```yaml
  tools:
    web_search:
      - query: "<what she asks>"
        returns: |
          <the result, as the tool would return it>
```

This also closes an existing harness gap that is independent of these 13 cases:
a Dreamer turn today reaches the REAL `web_search` and the real network — non-
deterministic, and billable. Every tool an eval turn can reach must be either
stated or refused; a tool call with no stated result is an ERROR, not a live
call (R8).

A parked case states:

```yaml
parked:
  reason: P-A | P-B
  why: |
    <why no anchor exists, or which seam is missing>
  returns_when: |
    <the observable condition that would unpark it>
```

**Every run reports the parked count beside the active one** — "150 total, N
active, M parked (P-A x, P-B y), K unanchored" — so a shrinking active set is
visible rather than discovered later. A suite that reports only what it ran is
how the previous one hid its own coverage.

## 8. Open questions for the operator

1. P-B says the tool-result seam is our work, not the room's. Is building that
   seam in scope for this rebuild, or its own piece of work with those 13 cases
   parked meanwhile? (They are parked either way; this decides who picks them
   up and when.)
2. Honcho replay is asynchronous and its derivation is not instant. How long
   must a rebuild wait before its peer cards are considered complete, and what
   does R6 measure to know?
3. Rebuilds are frozen. Does a changed doctrine/objective invalidate every
   existing rebuild at once, or do they coexist with their recorded
   `instruction_sha256` until moved by hand?
