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

### Not superseded — restate what stands

- **DEC-161's core guarantee is untouched and must survive this build:** a partial
  read is never presented as a complete one; `describe_coverage` measures against
  windows a *model* actually described; the `chunk_max` ceiling is recorded in
  `provenance` and said in the description. §3 below adds tests for the
  concurrent rewrite precisely because these properties are the ones a `gather`
  could quietly break.
- **DEC-114** (per-modality describers) — extended, not changed.
- **DEC-160** (one model pass per medium, three fields) — untouched.
- **DEC-138** (media as one content-addressed index) — untouched; no storage change.

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

## 4. ⚠️ Blocking check before the build is complete

**Does an identifier/screen cover model-generated speech transcripts?**

This is stated in `02-audio-in-the-video-path.md §5` and repeated here because it
is the one item that can be *wrong in a way that matters*. It is a verification
task, not a design decision:

1. Trace whether `scan_identifiers` / the render-time screen (`R-90`'s clause)
   receives the comprehension text on the path a description travels.
2. If yes — add a named test: a spoken digit-string in a video's audio does not
   survive into a rendered description.
3. If no — **stop and return to the operator.** The fallback is to keep audio on
   the *video* lane (where the ask is "what is this clip about") and leave
   `[media.describe.audio]` off, which is a narrower override than directed.
   Do not silently ship a widening of the egress surface.

## 5. Test obligations — the named tests this build owes

New (concurrency):

- `tests/unit/test_media_comprehender.py::test_windows_are_read_concurrently_not_in_sequence`
- `::test_segments_keep_window_order_under_concurrent_reads`
- `::test_one_refused_window_does_not_fail_its_siblings_when_read_concurrently`
- `::test_the_fan_out_never_exceeds_the_configured_concurrency`

New (audio):

- `tests/unit/test_vision_preprocess.py::test_the_video_view_keeps_its_audio_track`
- `::test_a_clip_with_no_audio_stream_still_produces_a_chunk`
- `tests/unit/test_media_comprehender.py::test_a_spoken_identifier_does_not_reach_the_description` *(pending §4)*

New (provider):

- `tests/contract/test_vision_client_contract.py::test_a_provider_with_no_key_fails_closed_and_never_falls_back`
- `::test_the_opencode_go_lane_sends_its_session_header`
- `tests/architecture/test_config_comes_from_the_template.py` — new keys present **and**
  the template-key-discipline gate for `OPENCODE_GO_API_KEY` + `chunk_concurrency`

New (budget):

- `tests/test_composition.py::test_the_derived_vision_timeout_never_exceeds_the_loop`
- `::test_moving_the_media_fetch_budget_moves_the_derived_timeout_with_it`
  *(the invariant DEC-10/ToolRegistry.budgets already encodes — pin it)*

Re-verify (existing, must still pass):

- `test_the_cap_binding_is_recorded_and_said`
- `test_a_capped_video_says_so_in_the_description_and_in_provenance`
- `test_a_tool_never_raises_into_the_agent_loop`
- the three `describe_*` map-gate tests (a modality appears only when enabled AND named)

## 6. Release fragment

Required by the repo gate even for a design-only branch (`type: "none"`), and
`scripts/check_release_fragments.py --base master` + `scripts/render_release_notes.py`
must both run green. Suggested slug: `media-read-lane.json`.

## 7. Checklist before merge

- [ ] Independent adversarial sub-agent review dispatched (**never self-reviewed** — conventions §2)
- [ ] Every `file:line` re-grepped on the final tree (conventions §5); the anchor table in `04` was built by grepping symbols, and must be re-grepped, not carried
- [ ] §4's egress check resolved, or the narrowed fallback taken with the operator told
- [ ] DEC-161 carries its `DEPRECATED IN PART` prefix pointing at DEC-195
- [ ] DEC-195 row added to `design/09-decision-index.md` with its test column
- [ ] Release fragment written, both gates green
- [ ] All commits in the worktree branch — master receives only an approved merge
