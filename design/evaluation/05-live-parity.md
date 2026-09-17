# Live parity — the reading Step 5 was waiting for

`02-the-rebuild` Step 5 says the delete needs one live run of the same corpus
through both layers, and that **a disagreement is the next thing to understand,
not a reason to keep both**. It was run on 2026-09-17. It disagreed. Here is
what it found.

## 1. The run

OmniRoute reached over an `ssh -L` tunnel to norty, `--model` naming the
DEPLOYED aliases (`nora-gate`, `nora`) read off norty's live `config.toml`.
Same corpus, same cases, same config object for both arms — `build_config` is
called once and `build_gate_probe` is handed it — so the only variable is the
ASSEMBLY.

| | old layer | new layer |
|---|---|---|
| first run, `real/*`, n=1 | **10/10** | **9/10** |
| after the finding below, twice | — | **15/16 agreement** both times, different case each time |

## 2. The finding: the corpus meant something it never said

`real/weather-sham-shui-po` answered `go` **5/5** on the old driver and `no_go`
**5/5** on the new one. Not noise. The prompts, captured at
`build_user_message`:

```
OLD  <m n="1" age="6m ago" from="Sam">今日好似好凍</m>
     <m n="2" age="3m ago" from="Aaron">深水埗而家幾多度？</m>
     <m n="3" age="just now" from="Kit">唔知喎</m>

NEW  all three from "Miles", 60 seconds apart
```

**Three people, where one asked and another said they did not know, is a room
question. One person saying all three is a monologue.** The gate is right both
times. The two prompts are different windows.

`eval_support._gate_records` rotated `("Kit","Sam","Aaron")[i % 3]` at
180-second steps. **Nothing in the corpus said so.** And the participation
corpus's bare strings meant the opposite — every line from the one mapped
member, 60 seconds apart (`seed_from_dict`).

> **One syntax, two meanings, one per driver.**

That is the bundle's own thesis appearing in the data rather than the code, and
it is worse than the code version: a byte-for-byte check of the move **passed**,
because the strings are identical. `tests/architecture/
test_corpora_are_rehomed_not_rewritten.py` was green the whole time.

### What was changed, and what was not

The gate corpus now **states** its speakers and its spacing. The three lines,
the three people and the ages are exactly what the labels were assigned
against; what changed is that the file says so instead of a driver. The pin now
encodes both old meanings, so it can catch this class next time — which it
could not before, and that is the reason it is written down twice.

## 3. The residual, and why it is not the same kind

Agreement was **15/16 on both runs, on a DIFFERENT case each time**:

| run | the one that disagreed |
|---|---|
| first | `closed/where-is-named` — old `go`, new `no_go` |
| second | `closed/settled-place-scrolled-out` — old `no_go`, new `go` |

**A disagreement that moves is variance, not a split.** Two different Noras
would disagree on the same case twice. The first run's case was then measured
at n=5 on both arms — `go go no_go go go` against `go go go go go` — which is
the same reading from the other direction: 4/5 on a case sitting on a boundary.

Both are `closed/*`, and the corpus's own header says that is the family built
to sit on the boundary (*"the same sentence without a name returned no_go while
with a name it returned go 5/5"*). They carry `sample: 1`, which for a
boundary case is arguably too few — but that is the corpus's call and this
bundle does not edit labels to make an arm agree.

**It is reported by measurement 1, the determinism control**, which is why that
measurement is printed above accuracy. And the committed parity test pins the
RATE rather than the name: pinning `closed/where-is-named` would have failed on
the second run, and a test edited after every run is a test nobody trusts.

## 4. What the two arms do NOT share, and it matters

`NO_CACHE_HEADERS` appears in `nora/app/eval_support.py` **and nowhere in
`nora/app/composition.py`**. So:

- the **old** gate probe is cache-bypassed;
- the **deployed** gate is not, and neither is the new layer.

The new arm is on the production side of that split, which is correct by the
invariant and is what `03-step0-results` §2.7 predicted. It also means
**sample-level counts from the two layers are not directly comparable** — a
gateway cache makes `0/5` and `5/5` exactly the shape it produces. The
agreement number in §1 is a per-case comparison, not a per-sample one, for that
reason.

## 4.1 The effective-n instrument, and its first reading

The old layer cannot answer the question §4 raises about itself: its run logs
carry no per-sample timing, so the effective n of any number already published
is unverifiable from its artifacts. The new layer records `elapsed_seconds` per
draw and measurement 0 prints the spread — **inside the "did the run happen"
section, because it is about whether the draws were real calls, not about what
they found.** No threshold is applied: what counts as too fast depends on the
model and the route, and a number baked in would be a proxy for the property.

**Its first reading does not fit, and is recorded rather than explained.**
Three gate cases, `sample: 2`, against the deployed `nora-gate` alias over the
tunnel:

```
0. THE RUN ITSELF — OK (6/6 draws)
   per-draw seconds: min 0.13  median 0.17  max 0.35
```

Every draw includes a full `build_dependencies` (~64ms) **and** the gate's
model call. Yet §1's parity run made 32 gate calls in 105 seconds — about 3.3s
each. Sub-second draws are an order of magnitude off that, and the obvious
reading is a gateway cache hit, which is exactly what §4 says these numbers are
vulnerable to.

**But it does not close.** The same run reports `real/weather-sham-shui-po` as
`1/2` on the determinism control — two different answers. A cache hit returns
the same bytes; two answers means two calls. So either these were real calls
that were ten times faster than the parity run's, or the two draws did not send
identical prompts (each composes a fresh subject, so the rendered ages differ
by however long the draw took) and only some hit the cache.

Both are checkable and neither is checked here. What matters is that **the
instrument produced a reading nobody predicted on its first run**, which is the
whole reason to carry the number instead of asserting `sample: 5` means five
readings. Resolving it is the check that must come before #324's arm is
unparked — otherwise that arm gets recomputed twice.

## 5. What this licenses, and what it does not

**Licensed:** the new layer reaches the real model, drives the deployed
component, and agrees with the old one on 15 of 16 cases once the corpus states
what it always meant. It found a defect the old layer could not have found
about itself.

**NOT licensed: the delete.** Three things are still open and each is a reason
on its own (`04-the-convention` §6):

1. the judge and rubric graders have not moved, so no adversarial corpus can be
   scored yet;
2. 23 cases are refused for missing seams in `nora.app`, two of which are not
   closed;
3. this parity run covered ONE of four entry points;
4. §4.1's timing reading is unexplained, and it bears on every sample count
   this layer will produce.

Step 5 remains the last step and it is still not this one.
