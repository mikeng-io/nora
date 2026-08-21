---
kind: spec
title: "Transfer: the fact tier and the Dreamer runtime"
description: "The fact tier's storage half is built, merged and deployed; its writer is not, because the Dreamer runtime does not exist. This hands both to one session. It carries what is NOT already in the specs: the verified deployed state, the four contract-suite defects and the one rule that subsumes them, the open items with their reasoning, the cross-session conventions that cost four collisions to learn, and the traps that cost a day each when rediscovered."
tags: [nora, transfer, facts, dreamer, runtime, handoff]
timestamp: '2026-08-21'
status: accepted
---

# Transfer: the fact tier and the Dreamer runtime

## 0. Read these first, in this order

1. `2026-08-20-fact-tier-design.md` — the fact tier. **§9.0 and §13.5 are the two sections most worth your time**; the first is a measurement, the second a correction.
2. `2026-08-21-dreamer-runtime-handoff.md` — the runtime, from the session that specced it.
3. `2026-08-20-dreamer-as-an-agent-design.md` — the keystone. **Its §4 act-verb list is four short**; re-derive from the registry (§5 below).
4. `2026-08-20-reflection-reasoning-continuity-design.md` — where all of this started. Note §6.3 and §6.8 are **superseded** by the fact tier's implementation.

**Read the artifacts, not descriptions of them.** Twice on 2026-08-20/21 that distinction caught a real defect: a `DreamSink` docstring promising a field the signature lacked, and a contract suite claiming a one-line join that was false. Both were caught by someone opening the file after being told what was in it.

## 1. State, verified 2026-08-21 08:03 against the running container

| | |
|---|---|
| `origin/master` | PR #19 merged (`1037270`) |
| deployed image | built `08:02:47`, container **healthy**, 0 errors since restart |
| DB migration | **`0025_fact_tier`** (was `0024`) |
| production tables | `fact`, `fact_candidate`, `fact_entity`, `fact_premise` — all **empty** |
| gate | 3815 passed, 0 failed; ruff / mypy 122 files / lint-imports 6-0 clean |

## 2. What is built, and what is not

```
[ Dreamer runtime ]   NOT BUILT
        |
        v  (would call)
[ fact adaptor ]      NOT BUILT — needs the runtime
        |
        v  propose / supersede
  DreamSink port      BUILT   nora/domain/ports.py (DEC-130)
  SqlFactStore        BUILT   nora/infra/sql/facts.py
  InMemoryFactStore   BUILT   nora/testing/fact_store.py
  fact tables         BUILT   migration 0025
  <facts> block       BUILT   nora/language/fact_render.py
  [facts] config      BUILT   ships false
  evals/facts/        BUILT   judge-free corpus
  R-54, DEC-129       LANDED
```

**Nothing writes a fact.** `<facts>` will render nothing until the adaptor exists. That is the entire remaining gap.

**`[facts] enabled` is deliberately still `false`, and flipping it today would be a lie.** No code reads it (`grep` confirms) and nothing writes `TagContext.facts`. Setting it true would make `config.toml` assert a subsystem is running when none is — the defect class this tier exists to prevent. Flip it when the adaptor lands, not before.

## 3. The one rule that matters most, learned four times

`tests/contract/test_dream_sink_contract.py` is shared by three tiers. Four separate defects were found in it, each a layer deeper, each by a session **reading a file it had not written**:

1. fact-tier literals hard-coded as shared fixtures
2. five tests reading `sink.rows`, an attribute only the fake has
3. the foreign-payload assertion missing entirely (impossible to write until a real store joined)
4. fact vocabulary (`because="contradicted"`) in a shared assertion

They collapse to one rule, now in that file's docstring:

> **Anything the shared suite needs that is not on the port must be supplied per implementation.**

Assertions are on the port → shared. Building a valid `Proposal` is not → per-implementation factory. Reading a row back is not → per-implementation adapter. Vocabulary is not → round-trip assertions, never literals.

**Joining is genuinely one `IMPLEMENTATIONS` entry** — verified by building a non-fact store against it and watching all 13 tests pass. If that stops being true, it is a defect.

## 4. Design rules you will be tempted to break

- **The runtime must never name `FactVerdict`.** `Proposal.payload` is `object` and the runtime never introspects it; provenance (`instruction_sha256`, `model`, `established_by`) rides on `Proposal` because it is true of every adaptor. The moment `payload` is typed, "one runtime, many adaptors" becomes false.
- **`isinstance`-check the payload at the sink.** The price of an opaque `payload`. Without it a wrong value travels a long way quietly.
- **Permissive at the port, strict at each sink.** `replaced_by` is nullable on `DreamSink` (a retired note is replaced by nothing) and refused by `SqlFactStore` (the `fact` CHECK is a biconditional). A port that hard-codes one tier's *cardinality* is the same mistake as one that hard-codes its *type*.
- **`source` is a warrant** — a URL or pointer to evidence, **never an id of a row in the tier**. Successors go in `replaced_by`.
- **No delete verb**, on the port or any implementation. Asserted on the Protocol.

## 5. Traps that cost a day each

1. **`create_agent` cannot build the Dreamer.** It requires a `ReplyGuard`, and `create_model_provider` hardcodes `tool_choice="required"` — so a loop with **no terminal verb** can never stop, because the guard ends loops by observing a terminal call. §4 of the keystone strips every act verb, and the terminal ones *are* act verbs. You need your own guard and your own stopping condition. Two tasks the keystone implied were free.
2. **The keystone's act-verb list is four short** — it omits `create_event`, `update_event`, `rsvp_event`, `cancel_event`. **Derive the read/act split from the tool registry**, never from prose. A plan copying that list hands a background agent four write verbs.
3. **`pulse_*` cannot be classified.** The read/act flag lives on `NativeTool`; `pulse_*` arrives over MCP and has no row. It is the family the motivating case most needs (2-of-5 gourds read as complete). Unresolved.
4. **`_dream_once`'s empty-batch branch** re-advances the watermark at its current value. Any new age-based trigger that fires at `lag == 0` needs that reset or it reruns every tick forever, 288×/day.
5. **The digest is read PER RUN, never cached at construction.** A digest computed once at process start keeps naming the corpus as it was at startup and **looks correct forever**. And hash only what was actually *sent* — binding provenance to text that never reached the model is the defect `room_portrait` is excluded from DEC-125 for.
6. **A watermark renamed during migration** gets `watermark_for → 0` and re-reads every room from the beginning.
7. **`compare_metadata` is blind to CHECK constraints.** A CHECK in `schema.py` that no migration wrote drifts silently. Put every CHECK in the migration AND `schema.py`, and assert it with a test that attempts the bad INSERT.

## 6. Open items on the fact tier — real, deliberate, unhandled

- **Transitive supersession.** A fact deduced from a since-superseded premise stands on nothing. `fact_premise` makes the dependency *queryable* (reverse index on `premise_fact_id`); nothing acts on it.
- **`warrant_kind='premises'` implies a premise row — enforced by NOTHING.** It spans tables, so it is not a row CHECK. Stated plainly in the migration, `schema.py` and §13.5 rather than faked with a trigger. The writer must maintain it.
- **Entity resolution is unbuilt.** `fact_entity.aliases` exists; nothing populates it. `big walk` / `Big Walk` / 大步行 must resolve to one subject or the tier fragments into near-duplicates that can never contradict each other. §13.1.
- **Abduction and volition have nowhere to live.** `warrant_kind` has `observation` and `stated` values; nothing produces them. §13.5.
- **`fact_render` shows raw ids** for entity-objects — no join to `canonical_name`. Closes with entity resolution.
- **A malformed `target` degrades to 0 rows** while a malformed `replaced_by` raises. Deliberate and documented; revisit if it surprises anyone.

## 7. Cross-session conventions, which cost four collisions to learn

- **Announce the NUMBER, not just the file.** Two DEC collisions happened because both sides appended cleanly to `design/09-decision-index.md` — a clean auto-merge is exactly the condition under which a number clash is invisible. Highest DEC is **130**; R-54 is the fact tier's.
- **Migrations:** `0025` is taken. The note tier is assigned `0026`.
- **Read back before claiming.** Every merge, deploy and fix in this workstream was verified against the live state, not the tool that reported it. `0025` was verified never-applied before being amended in place; the deploy was verified from the running container and database.
- **Send a shared port's signature to the second implementer before declaring it.** That practice caught the missing `replaced_by` field.

## 8. What the fact tier's own evidence says

R-53 forbids reading a source's silence as denial — in prose, in doctrine. It shipped and could not be shown to work: measured, the eval grader **passed the exact failure 3/3 at confidence 1.0 on two different models** while quoting a denial the source never made. That is why the rule moved into a shape (`not_covered` has no reachable `supersedes`) rather than into more prose.

Two claims in §9.0 were later **corrected in place** rather than deleted — the mechanism was misdiagnosed and one table row did not support the conclusion drawn from it. Read the correction; it is a better guide to how to reason about that eval than the original was.
