---
type: design
title: "Decisions, Supersessions, and Test Obligations"
description: "The DEC row this bundle claims, the clauses it overrides (named, not deleted), the R-rules affected, and the named tests the build owes."
tags: [nora, design, decisions, index, supersession, tests]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Decisions, Supersessions, and Test Obligations

## 1. The DEC row

**DEC-195** is the next free number — verified against `design/09-decision-index.md`
(the table ends at DEC-194) and against a repo-wide grep for `DEC-195` in both
`design/` and `nora/`, which returned nothing. Per the bundle conventions, the
number comes from the index, never from counting files.

Proposed row text (to be added to `design/09-decision-index.md` at PR time, in
the existing table format with its test column):

> **DEC-195 — The media read lane reads its windows concurrently, keeps the video
> path's audio, gains a third describer provider, and raises the per-call vision
> budget to a value measured latency already required** (operator 2026-09-22,
> `design/media-read-lane/`). Four changes decided together because they share one
> arithmetic. **(1)** `read_video_windows` gathers its windows instead of awaiting
> them in sequence, bounded by a semaphore, with ORDER preserved from
> `VideoWindow.start_seconds` and per-window failure degrading exactly as the
> sequential version did — a `gather` that turns one refused window into a
> refused clip is a regression, not a rewrite. **(2)** `_video_args` stops passing
> `-an`; **this overrides DEC-161's silent-chunk clause by operator direction**
> (2026-09-22). **(3)** `DescriberConfig.provider` gains `"opencode-go"`, a direct
> client to `opencode.ai/zen/go/v1` requiring a mandatory `x-opencode-session`
> header the existing lanes do not have; `[media.describe.video]` moves to
> `mimo-v2.6-flash` — **which also supersedes DEC-161's «no new provider» scope
> limit**, at 2.7× lower video token cost and ~two orders of magnitude more
> request quota than K3, with comparable accuracy. **(4)** The derived per-call
> vision budget was 36 s against measured 57 s-clip latency of 46.2 s on the
> INCUMBENT model — a defect on master, not a consequence of this change — so
> `media_fetch` budget 4 → 3 and `vision_timeout_seconds` → 90 (derived 48 s),
> keeping `budget × derived < loop`. Plus `vision_max_tokens` 1024 → 4096, without
> which MiMo returns `finish_reason="length"` on every long clip.
> **The egress treatment of transcribed speech is an open blocking check (§4),
> not a settled property of this decision.**

## 2. Supersessions — named, never deleted

### DEC-161, clause `-an`

```
design/09-decision-index.md (DEC-161):  "…and `-an` stays so the chunks are
silent: audio extraction plus STT was proposed and rejected as scope."
```

**Overridden.** The operator's direction is explicit and recorded: 「audio 亦都 enable
佢啦，直接啟動佢 override 之前個 decision」. The reasoning in DEC-161 was *scope
discipline* — a deliberate bound on one change, not a claim that audio is
undesirable or unsafe. It is therefore superseded by a later decision rather than
refuted, and the row should carry a `DEPRECATED IN PART 2026-09-22 — see DEC-195`
prefix in the established style (compare DEC-133, DEC-75), **not** be rewritten.

### DEC-161, clause «no new provider»

```
design/09-decision-index.md (DEC-161):  "Each window goes to the EXISTING
describer (kimi direct) unchanged — no new provider…"
```

**Superseded as a scope limit.** That clause kept the #117 chunking change small.
This bundle deliberately takes the opposite trade — a new provider *is* the change
— for a cost and quota win that did not exist when #117 landed. The per-modality
provider field itself is **DEC-114's** design, so adding a third value *extends*
DEC-114 rather than contradicting it.

### DEC-114 clause (3) — the one I mislabelled, and the review caught it

```
design/09-decision-index.md (DEC-114): «The audio prompt asks for the GIST, never a
transcript, and that is a SAFETY DECISION NOT A STYLE ONE. STT is excluded here
because a verbatim transcript is a materially larger R-18/R-28 egress surface —
`_serve_media`'s own docstring records that `scan_identifiers` does not catch a
bare local number, and people say addresses and phone numbers aloud.»
```

**The first version of this bundle said DEC-114 was "extended, not changed". That was
wrong, and dangerously so** — it flattened a decided *safety* boundary into a scope
limit. Enabling transcription supersedes this clause, and the override must be visible:
**DEC-114 gets a `DEPRECATED IN PART 2026-09-22 — see DEC-195` prefix on clause (3)**,
and the row must say *why* the boundary moved, so a later reader sees a deliberate
safety override rather than an accident.

Operator direction covers the override; it does not remove the obligation to state the
residual exposure (`02-§5`: a bare local number, an address, or a number spoken as
words is **not** caught, by design).

### Not superseded — restate what stands

- **DEC-161's core guarantee is untouched and must survive this build:** a partial
  read is never presented as a complete one; `describe_coverage` measures against
  windows a *model* actually described; the `chunk_max` ceiling is recorded in
  `provenance` and said in the description. **⚠️ But see `01-§5a` — the review showed
  the caveat is currently WRONG in a combined-failure case, so "preserve today's
  behaviour" preserves a pre-existing inaccuracy.** The build owes a fix, not a
  preservation.
- **DEC-114 (1), (2), (4), (5)** — the modality split, the content-block shape, the
  one-client-one-budget rule, and the optional/defaulted table discipline all stand.
  Only clause (3) moves.
- **DEC-160** (one model pass per medium, three fields) — untouched.
- **DEC-138** (media as one content-addressed index) — untouched. The review confirmed
  blob keys hash the **original plaintext bytes** (`nora/infra/blobs/filesystem.py:100-113`),
  so ffmpeg output and the added audio track do not affect storage identity.

## 3. R-rules affected

| rule | effect |
|---|---|
| **R-33** — a stored image is described only from THIS conversation's custody, under the vision call's OWN timeout and byte ceiling | **Amended by arithmetic, not by text.** The "own timeout" becomes 48 s instead of 36 s. The rule's *shape* is unchanged; its number moves. Must be re-verified that the byte ceiling is still applied per-modality (`describe_max_bytes` already keys per modality). |
| **R-51** — a media description is never invented | **Becomes MORE load-bearing, not less.** With audio, a transcript is a *new* class of model-generated text that can be confidently wrong. The `minimax-m3` confabulation measured in this session — a dropped video that two models nonetheless described in fluent, specific, mutually-contradictory detail — is the exact failure this rule exists for. No text change needed; the build must not weaken it. |
| **R-90** — `messages.body` carries a LABEL and a HANDLE only; the comprehension is joined at RENDER time and screened | **The screening half is where §4's open question lands.** R-90 already asserts the comprehension is screened. If speech transcripts are a new content class, R-90 is the rule that should already cover them — the build's job is to *verify* that, and add a named test if it does not. |
| **R-27/R-5** (ERROR is never PASS; partial reads never sold as complete) | Both are why §4's `finish_reason="length"` behaviour matters: `_checked` already raises rather than serving a truncated description. Preserve. |

**No new R-rule is proposed.** Everything here is either an existing rule's number
moving or an existing rule's coverage being verified. A new rule would need a
behaviour that is currently unconstrained — I did not find one, and inventing a
rule for an already-covered case is the over-specification this design should
avoid. **If the adversarial review finds an unconstrained behaviour, that is the
finding most worth having.**

## 4. Measurement obligations — the gate this bundle now carries

**Added after adversarial review.** The reviewer's sharpest structural point was that
the bundle had *test* obligations but no *measurement* obligations, and that the
headline settings were never validated at production shape. These come first:

1. **A successful read at production shape:** an **audible 90 s window** (the real
   chunk size — a 60 s Story is *below* it, so it is not the worst case), the proposed
   timeout, bounded concurrency, checking **`finish_reason` and usable content** — not
   just HTTP 200. The existing concurrency probe used a 600 s client timeout and
   recorded only status + prompt tokens, so it proved requests *overlap*, not that a
   production read *succeeds*.
2. **The per-invocation budget**, covering queued waves and preprocessing — see
   `04-§3a`: the invariant is proven over model calls, not tool invocations.
3. **Observability.** `docs/observability.md:480-489` records that vision reads export
   **zero token usage and no per-window spans**, and `client.py:355-358,403-405` applies
   the shared ceiling to *both* text synthesis and media reads. So the new token-cost
   claim **cannot currently be substantiated from production**, and queueing, per-window
   latency and preprocessing cannot be told apart. Either add the emission, or state
   explicitly that cost and concurrency remain unmeasured.
4. **`vision_max_tokens` is a per-request ceiling**, and raising it to 4096 affects
   images, audio, video windows *and* synthesis — not only MiMo video. Say so, and note
   that it is an allowance per request, not an aggregate budget.

## 5. Test obligations — the named tests this build owes

New (concurrency):

- `tests/unit/test_media_comprehender.py::test_windows_are_read_concurrently_not_in_sequence`
- `::test_segments_keep_window_order_under_concurrent_reads`
- `::test_one_refused_window_does_not_fail_its_siblings_when_read_concurrently`
- `::test_the_fan_out_never_exceeds_the_configured_concurrency`
- `::test_a_cancelled_read_while_windows_are_complete_does_not_report_a_whole_clip`
  *(the missed case: `return_exceptions=True` does not make cancellation yield a
  partial `(segments, read)`)*
- `::test_a_capped_read_with_a_middle_gap_does_not_claim_it_watched_the_first_n_seconds`
  *(`01-§5a` — the pre-existing caveat defect)*

New (audio):

- `tests/unit/test_vision_preprocess.py::test_the_video_view_keeps_its_audio_track`
- `::test_a_clip_with_no_audio_stream_still_produces_a_chunk`
- `tests/unit/test_media_comprehender.py::test_the_automatic_comprehension_question_asks_for_speech`
  *(the `COMPREHEND_QUESTIONS` family — `02-§6`)*
- `::test_a_bare_local_number_in_a_transcript_either_does_not_reach_the_description_or_is_documented`
  *(see §6 — the test cannot be "it never survives", because the current screen
  guarantees it survives)*

New (provider):

- `tests/contract/test_vision_client_contract.py::test_a_provider_with_no_key_fails_closed_and_never_falls_back`
- `::test_the_client_built_by_composition_sends_the_session_header`
  *(built by **composition**, not constructed in the test — `03-§3`)*

New (persisted data — `02-§8`):

- `::test_an_already_comprehended_video_is_not_silently_re_read_after_audio_is_enabled`
- `::test_reverting_the_provider_leaves_persisted_transcripts_in_a_defined_state`

New (budget):

- `tests/test_composition.py::test_the_derived_vision_timeout_never_exceeds_the_loop`
- `::test_moving_the_media_fetch_budget_moves_the_derived_timeout_with_it`

**Corrected gate names.** The first version cited
`tests/architecture/test_config_comes_from_the_template.py` for new-key presence. That
file checks *how tests obtain config* (`:22-34`), not template parity — it can pass
while a new setting is absent from the template. The actual parity checks are
**`tests/test_env_template.py:293-336`**, and note those can **skip** without a
deployment config (`:71-94`): a skip is not a pass, so report them distinctly.

Re-verify (existing, must still pass):

- `test_the_cap_binding_is_recorded_and_said`
- `test_a_capped_video_says_so_in_the_description_and_in_provenance`
- `test_a_tool_never_raises_into_the_agent_loop`
- the three `describe_*` map-gate tests (a modality appears only when enabled AND named)

## 6. The egress residue — recorded, decided by the operator

See `02-§5` for the finding. Summary: the screen runs on this path (that question is
closed), but `nora/agent/egress.py:45-46` **deliberately does not match
free-standing digit runs** and its `_E164` pattern at `:67` **requires a `+`** — so a
bare local number said aloud is not caught, which is exactly what DEC-114 clause (3)
said when it chose gist-over-transcript.

**This is the operator's call, with the finding attached.** Either the pattern is
extended for spoken content (bare HK-format numbers, digits-as-words), or the residual
exposure is accepted and documented in the DEC-114 prefix. **Do not let the build
silently pick the second by writing a test that asserts current behaviour.**

## 7. Release fragment

Required by the repo gate even for a design-only branch (`type: "none"`), and
`scripts/check_release_fragments.py --base master` + `scripts/render_release_notes.py`
must both run green. Suggested slug: `media-read-lane.json`.

## 8. Checklist before merge

- [ ] Independent adversarial sub-agent review dispatched (**never self-reviewed** — conventions §2)
- [ ] Every `file:line` re-grepped on the final tree (conventions §5); the anchor table in `04` was built by grepping symbols, and must be re-grepped, not carried
- [ ] §4's egress check resolved, or the narrowed fallback taken with the operator told
- [ ] DEC-161 carries its `DEPRECATED IN PART` prefix pointing at DEC-195
- [ ] DEC-195 row added to `design/09-decision-index.md` with its test column
- [ ] Release fragment written, both gates green
- [ ] All commits in the worktree branch — master receives only an approved merge
