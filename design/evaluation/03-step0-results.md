# Step 0 — run, 2026-09-17. The divergences reach the model.

`00-overview` §8 set the refutation: *if the divergences do not reach the model,
the seven are one Nora wearing seven coats and this bundle is withdrawn.*

**Two components checked. Both reach the model. The refutation does not fire.**

Also settled here, because it was the reviewers' loudest worry: **the cost
objection is false.** §3.

---

## 1. `RelevanceGate` — the prompt differs, measured

`composition.py:2692` passes 9 kwargs; `eval_support.py:980` passes 8 — no
`persons`, no `media_join`, plus a `capabilities` production deliberately dropped.

Both paths' transcript rows run through `render_context` (`relevance_gate.py:405`),
which is the gate's whole model-facing prompt. Same window, both shapes, per
`relevance_gate.py:809-817`:

```
eval-built       : 539 bytes
production-built : 559 bytes
IDENTICAL        : False
```

```
eval        <m n="1" age="366d ago" from="miles">睇下呢張</m>
production  <m n="1" age="366d ago" from="Miles">睇下呢張 [a photo of a menu]</m>
```

Two differences, both model-facing:

- **`from=`** — the eval gate gets `miles`, the platform's self-set profile name.
  Production gets `Miles`, the operator-canonical name, via `persons.name_transcript`.
- **the body** — production carries the media description; the eval gate does not.

The second is the one production documents on the line that passes it: an
image-only message reads as an **empty body**, and `render_context` drops the row
— *"a systematic under-admission"*. **A window that is one photo is a window the
eval gate cannot see at all.**

Read that beside the gate's own live reading — `no_go` on 35/35 windows over the
retained window, which is a **production** Prometheus number. Anyone tuning the
gate against the eval gate is tuning a prompt that cannot see photographs, to fix
a verdict measured on one that can.

## 2. `FirePath` — three prompt blocks are empty or absent

`composition.py:2458` passes 36 kwargs; `eval_support.py:1703` passes 21. Four of
the missing fifteen reach the prompt, and three do so through integers that
default to zero:

| | production (`config.example.toml`) | eval `FirePath(21)` | effect |
|---|---|---|---|
| `self_card_conclusions` | **8** | not passed → **0** | `<self_card>` carries nothing |
| `people_in_window_conclusions` | **3** | not passed → **0** | — |
| `people_in_window_speakers` | **4** | not passed → **0** | **`<people_in_window>` is not rendered at all** |
| `decisions` | `decision_log` | not passed | `<loop_decisions>` carries nothing |
| `media_join` | wired | not passed | no media description in the transcript |

The third is not a thinner block, it is an absent one — `nora/app/fire.py:1122`:

```python
if self._people_in_window_speakers <= 0:
    return ()
```

**So `proactive_act`, which is graded on "what the room heard", measures an agent
that does not know who is in the room, carries no self-account, has no memory of
its own prior decisions, and cannot see photographs.**

### And this is the one nothing guards

`tests/architecture/test_assembler_parity.py` compares `composition.py` against
`offline.py`. **Both pass `FirePath(36)`.** It is green, it has always been green,
and it never looks at `eval_support.py`.

## 3. The cost objection is false — and the repo already solved it

Both reviews flagged "driving `build_dependencies` per case needs a fresh Postgres
plus migrations" as the thing most likely to kill the rebuild. Measured:

```
fresh database + build_dependencies, per case:
  min 61ms    median 64ms    max 84ms
  40 windows -> ~2.6s of subject setup
```

**No container churn.** `tests/conftest.py` holds one session-scoped container and
one session-scoped template database at migration head; each case gets
`CREATE DATABASE t_<uuid> TEMPLATE pg_template` — *"one connection and a version
check instead of 27 revisions"*, in the fixture's own words. No per-case
container, no volumes left behind.

So **"one Nora, obtained per case" is affordable at 64ms**, and the architecture
does not need a session-scoped subject or a state-reset seam. A case can be
plain data, which is what `01-boundaries` §2 asserted and could not price.

*(Both reviews and this bundle's earlier drafts described this as "a fresh
Postgres per call". That description was wrong and was repeated without being
checked. The operator asked why it would not be one container with many
databases; it already is.)*

## 4. What this settles, and what it does not

**Settled:**
- The divergences reach the model, in two of two components checked.
- `FirePath`'s divergence is block-level, not content-level.
- The cost objection is false.
- `00-overview` §8's refutation does not fire.

**NOT settled — five components unchecked**: the two `DreamerRuntime`s,
`RegistryToolSource`, `LiveAgentRunner`, and `build_offline_pipeline(14)`. Each
should be checked the same way before its suite is rebuilt, and each could still
come back empty.

**Still true** (`00-overview` §7): not every divergence is a defect.
`participation_gate=None` is deliberate and right. The finding is that seven
assemblies exist, nothing relates them, and **nothing detects when one drifts** —
which §2 demonstrates with a parity test that is structurally unable to see the
largest divergence in the tree.

## 5. Method, so this is re-derivable

Both probes were temporary test files, run and deleted; neither is in the tree.

- **Gate**: build the row set both ways per `relevance_gate.py:809-817` (eval =
  raw rows; production = `media_join.enrich` then `persons.name_transcript`),
  render both through `render_context`, compare bytes.
- **FirePath**: read the four arguments' defaults against
  `config.example.toml`'s values, and `fire.py:1122`'s guard.
- **Cost**: `build_dependencies(cfg)` under `tests/test_composition.py`'s `cfg`
  fixture, six cases each with its own function-scoped `pg_url`.

A permanent version of the first belongs in the rebuild as the check that the one
subject really is one — `00-overview` §2's "nothing detects when one drifts" is
the gap it would close.
