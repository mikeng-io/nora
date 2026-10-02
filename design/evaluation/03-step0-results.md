# Step 0 — complete, 2026-09-17. Four of seven reach the model.

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


`00-overview` §8 set the refutation: *if the divergences do not reach the model,
the seven are one Nora wearing seven coats and this bundle is withdrawn.*

**All seven checked. Four reach the model, two come back empty, one has no
prompt. The refutation does not fire.**

| subject | reaches the model? |
|---|---|
| `RelevanceGate` | **YES** — the sender's name and the media description (§1) |
| `FirePath` | **YES** — three prompt blocks empty or absent (§2) |
| `RegistryToolSource` | **YES** — changes what the tools answer (§2.5) |
| `build_offline_pipeline(14)` | **YES** — seven collaborators unpassed (§2.5) |
| `DreamerRuntime(9)` | **no** — inert (§2.6) |
| `DreamerRuntime(11)` | **no** — inert (§2.6) |
| `LiveAgentRunner(4)` | **no** — a metric sink (§2.6) |

**The two that come back empty are the most useful part of this result**, and §4
says why.

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

## 2.5 `RegistryToolSource` and `build_offline_pipeline` — also reach it

**`RegistryToolSource(9)`** omits `online_scope_for` and `origin_rooms`. Neither
is a prompt input directly; both change **what a tool answers**, which is what
the model reads next:

- `online_scope_for` resolves who is online for a unit. Without it
  `online_now` cannot answer. `dreamer.py:1055-1070` carries a paragraph on why
  this resolver must not be handed the wrong scope — *"the widened decide-turn
  domain reaching a background pass it was never meant to"*.
- `origin_rooms` resolves a note's `established_in` (`tools.py:7060`), i.e. which
  room a note belongs to.

**`build_offline_pipeline`** takes 21 parameters; `evals/harness.py:612` passes
14. Unpassed: `media_join`, `debrief_wiring`, `standing_event_wiring`,
`participation_gate`, `participation`, `tag_trace_store`,
`extra_engage_policies`.

`participation_gate` is the documented, deliberate one and stays right. The rest
are not argued anywhere.

**`media_join` now appears in three of the four**: the gate, `FirePath`, and here.
One missing collaborator, three different subjects, no relation between them —
which is the bundle's thesis stated as a fact rather than an argument.

## 2.6 Three come back EMPTY, and this is the important half

| | missing | why it is inert |
|---|---|---|
| `DreamerRuntime(9)` | `grants`, `tool_budget_overrides` | the note-candidates adaptor declares `tools = frozenset()` (`note_candidates.py:872`). No tools, so a grant and a per-tool budget override are both no-ops. **And production gives note-candidates no grant either** — `dream_grants` holds only proactive and culture. |
| both `DreamerRuntime`s | `store_timeout_seconds` | `DEFAULT_DREAM_STORE_TIMEOUT_SECONDS` **is 15.0**, and `config.example.toml`'s `[dreamer] store_timeout_seconds` **is 15.0**. Not passing it yields the same number. |
| `LiveAgentRunner(4)` | `metrics` | a sink for `nora_tagtrace_truncations_total`. Changes where a counter lands, not what the model reads. |

An earlier draft of `00-overview` headlined the `DreamerRuntime` difference —
*"the two are not the same object … and nothing says so"* — and both reviews
found that **true and empty**. It is recorded here as measured rather than
quietly dropped.

## 2.7 A divergence this bundle's method structurally cannot see

Every finding above is a **kwarg diff** — what collaborators a component is built
with. That method has a blind spot, found 2026-09-17 by the culture-tier session
while checking its own arm:

**Which eval clients bypass the gateway cache is a divergence from production,
and a kwarg diff cannot see it, because it is a HEADER.**

```
eval_support.py:171   NO_CACHE_HEADERS = {"x-omniroute-no-cache": "true"}
eval_support.py:920   applied  <- the SDK gate arm (#186)
eval_support.py:1014  applied  <- GateProbe
evals/run.py:590      applied  <- the judge
```

`build_live_runner` and `build_live_participation_runner` (`:273`, `:291`) call
`create_client(base_url=…, api_key=…)` with **no `default_headers`**. So the gate
and the judge are cache-bypassed and **the reply subject is not.**

Two consequences:

**1. `#186`'s absolute numbers are cache-bypassed readings.** ADR 0011 says so
itself — *"it does mean the 6/6 reading is a cache-bypassed one"*. The header is
on BOTH its arms so the 6/6-vs-1/6 comparison holds; neither number describes
production.

**2. For a reply suite with `sample: 5`, the effective n is unverifiable from the
outputs alone.** A case returning `0/5` or `5/5` is exactly the shape a cache
produces and is indistinguishable from genuine determinism. One varying case
(`2/5` was observed) proves the cache is not total; it cannot establish that any
other case drew five times. And `sample: 5` exists precisely because *"one draw
of a stochastic process is not a reading of it"*.

**The split was not chosen.** The header is on the three clients somebody
happened to be holding when they hit a caching problem — `run.py:580-586` records
it (*"judge sample 1 took 7558ms, samples 2 and 3 took 35ms and 66ms, and all
three were byte-identical including their usage"*) — and nothing relates that set
to the set of clients that make repeated identical calls. It runs along
gate-vs-reply rather than along anything anyone decided.

**Nothing in the tree audits it**, and the run logs carry no per-sample elapsed
time, so it cannot be settled from existing output. Per-sample timing on the case
line would make it answerable for free on the next run — the latency signature is
how `run.py:580` found it in the first place.

### What this says about Step 0's method

Step 0 compared **constructor arguments**. It found four real divergences and
correctly cleared three. It could not have found this one, and there is no reason
to think it is the only such item: anything carried on the client rather than in
the composition — headers, timeouts set at the transport, retry policy, base URL
— is outside what §1's table can express.

**So §1 is a floor on the divergence, not a measurement of it.** The rebuild's
"one Nora" has to mean one *client* as well as one composition, and this bundle
had not said so.

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

## 4. What this settles

**Settled:**
- **Four of seven** subjects diverge in bytes or tool answers that reach the
  model. `FirePath`'s divergence is block-level, not content-level.
- **And §1's table is a FLOOR, not a measurement** — §2.7 is a divergence the
  kwarg method structurally cannot see, and nothing says it is the last one.
- **Three come back empty.** They are not defects.
- The cost objection is false; "one Nora per case" costs 64ms.
- `00-overview` §8's refutation does not fire.

**And the two empty results are the sharpest evidence in this bundle.**

Nobody could tell in advance which four mattered. The `DreamerRuntime`
difference *looks* like the most alarming — two runtimes, different signatures,
one suite each — and it is inert. `store_timeout_seconds` *looks* inert and had
to be checked against a constant to know. `media_join` appears in three subjects
and is load-bearing in all three.

**That is the finding.** Not "the eval layer is broken", but: **seven assemblies
exist, four of them measure something the deployment does not send, three do not,
and nothing in the tree distinguishes them.** Every one of these seven answers
had to be derived by hand today. A rebuild is justified not because all seven are
wrong, but because **which ones are wrong is unknowable without this exercise,
and nothing prevents the answer changing tomorrow.**

**Still true** (`00-overview` §7): not every divergence is a defect.
`participation_gate=None` is deliberate and right, and now so are the three in
§2.6. The finding is that nothing detects when one drifts — demonstrated by a
parity test structurally unable to see the largest divergence in the tree (§2).

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
