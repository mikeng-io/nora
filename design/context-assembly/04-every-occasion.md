---
type: design
title: "Every occasion — no model prompt is built outside context assembly"
description: "The operator's rule (2026-10-01): every agent in Nora assembles its prompt through context assembly, no exception. Why the old census could not enforce it, what the tree looks like when discovered instead of listed, why the Cognition session fails (its input contradicts its own validator), and the six steps that close it, each with its measurement."
tags: [nora, context, prompt, blocks, assembly, cognition, drift]
timestamp: '2026-10-01'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 03-sequencing.md, rel: extends, provenance: asserted}
---

# Every occasion

## 1. The rule, and why it was not a rule

**Operator, 2026-10-01: every agent in Nora must assemble through context
assembly. No exception, or it goes back to the original chaos, everything
built differently.**

`03-sequencing.md` left one question open on purpose: whether a seventh
occasion joins the registry ("should not be swept in silently"). Nothing
answered it, and occasions kept arriving: note-merge (DEC-202), the Instagram
summons, the Cognition session. The census that was meant to hold the line,
`tests/architecture/test_the_registry_is_exhaustive.py`, read a hand list of
five files. **A list of occasions cannot catch an occasion nobody added to
it.** The rest of the architecture suite (`test_one_block_injector.py`,
`test_one_transcript_renderer.py`, `test_one_row_renders_one_line.py`) each
catch one symptom (a hand-written tag pair, a `.body` interpolation, a direct
record read) and a prompt in plain prose that avoids all three passes every
one of them. That is exactly what the Cognition session is.

## 2. The tree, discovered (measured 2026-10-01, source at `ab0ac6bb`)

| class | occasions |
|---|---|
| declared and assembled (`_BLOCKS` and `assemble`) | tag, fire's blocks, relevance gate |
| declared, joined by hand (`_BLOCKS`' `order` is a comment) | proactive, culture, note-candidates, note-curator |
| `ContextBlock`s, no table | note-merge, the debrief and the Standing fold, the reaction picker |
| hand-built text | **Cognition**, the Instagram summons, fire's header paragraph |
| not a context | vision describe, authored doctrine and skills, the `media_fetch` question |

## 3. Why Cognition fails: its input contradicts its output

Read from the trace of 2026-10-01 09:08 and the code:

- The prompt shows a message as `[#7998]`. `note_create` requires the id as a
  bare `[7998]` and an `(<names>)` attribution (`nora/domain/note_body.py`).
  The tag turn has a remap, `resolve_positions`; Cognition has none, and no
  test passes a rendered id through `note_create`. The session refused three
  times (`section_order`, `uncited`, `misattributed`) before it landed.
- Note-candidates and the curator show `n="<row id>"`, so the id shown is the
  id required. Cognition is the one Dreamer pass that does not.
- The grammar (`(Names)`, `## Owed`, `## Ruled out`) is taught nowhere in its
  objective; the model learns it from refusals. The shared channel for "what
  this block is and how to read it" is the block's type description in
  `nora/domain/block_types.py`; Cognition has no blocks and so no description.
- Group text is not escaped (R-13); the other renderers do.

## 4. The enforcer

`tests/architecture/test_every_model_call_is_a_declared_occasion.py` finds, by
AST and with no list of occasions, every module that calls a model (under the
name it imported the call by) and every class or function that defines
`inputs`, and demands each be declared: a module-level, non-empty `_BLOCKS`
and a call to the `assemble` imported from `nora.domain.blocks`. Otherwise it
must be a named MECHANISM, RUNNER (its builders named, each declared, and for
`imports` runners really imported) or EXEMPT with a reason. A PENDING ledger
names today's twelve failures and the step that removes each. It is pinned to
an exact size (`CEILING`), so paying one down and adding one are both visible
in the diff; a stale entry is refused; step 6 deletes the ledger. The census
test now finds its occasions instead of listing them.

An independent review of the first version (PR #675) found, reproduced and
closed: an `assemble` matched by name alone (a local function or
`ContextAssembler.assemble` passed), an empty or function-local `_BLOCKS`
passing, a runner's named builder never checked against its imports (re-pointing
laundered a debt), a ceiling that was a cap and not a ratchet, aliased and
`self._runner.invoke` doors missed, and prompt text hand-built inside runners
(fire's headers, the debrief's `_prompt`) invisible. The last is not detectable
by the net, so it is recorded by hand in `KNOWN_HAND_BUILT` and each entry is a
ledger debt.

**What it still cannot see**, stated so review covers it: a NEW hand-built text
inside a runner or mechanism (found by a person, then recorded); a model door it
does not know (Honcho's `chat`, a dialectic query to a memory service's own
model, and image generation); a model call reached only through an imported
helper; and a `_BLOCKS` walked by `assemble` over a list that does not come from
it.

## 5. The steps

| step | change | model-visible? | measured by |
|---|---|---|---|
| 0 | the enforcer, red first, PENDING = today | no | the test; probe: drop an entry, the test names the module |
| 1 | shared pieces: `assemble(sep=)`, one `today` builder, one row-id window builder (no model-visible change; goldens) | no | goldens captured from the code before the refactor: each existing occasion renders byte-identical |
| 2a | the four adaptors that join by hand walk `assemble` (proactive, culture, note-candidates, note-curator; goldens; table `order` fixed to match what the model reads today, never the other way round) | no | golden tests (step 1's criterion) Two known differences from the hand join, to handle explicitly: `assemble` drops an empty-body block, so culture's and note-candidates' self-closing `<room .../>` needs a form that survives, and proactive's `loop_decisions` is empty when no decision log is wired (a hand join keeps a trailing separator). |
| 2b | declare `_BLOCKS` for note-merge, the reaction picker and the debrief/Standing fold, moving the debrief's hand-built `_prompt` text into its renderer (goldens) | no | golden tests (step 1's criterion) |
| 3 | Cognition onto blocks: shared note index carrying the real note id, event and rollup renderers, the char budget as a row-selection pre-step with a cut attribute, `_BLOCKS`, block-type descriptions carrying the note grammar, escaping, the renderer moved out of `nora/domain` (model-visible: id-as-rendered test, replay of recorded sessions, cognition `tool_selection` corpus) | **yes** | (a) a test that an id as rendered passes `parse_note_body`, which reverting the change turns red; (b) the recorded sessions (each row keeps its situation and agenda) replayed through both renderers, refusal rate and close parity read, n small so signal only; (c) the cognition `tool_selection` corpus |
| 4a | the Instagram summons' prompt onto blocks (live path) | partly | golden tests; an arm where output changes |
| 4b | fire's five header paragraphs as one typed builder (live path) | partly | golden tests; an arm where output changes |
| 4c | the reaction picker via `speaker_label` (a `:` in a name forges a row today), the debrief's last-wins `by_ts`, five hand-rolled ages, tool-result transcripts in `nora/agent/tools.py`, the vision summarizer's unframed text | partly | golden tests; an arm where output changes |
| 5 | withdrawal (`render_supported`) in the seven Dreamer prompts that push a Standing or culture head (live, disclosure) | yes | tests per builder; whether those adaptors are meant to be exempt is UNVERIFIED |
| 6 | structural close: delete the ledger, the occasion-joining rule, decision row, AGENTS.md line | no | `test_nothing_is_pending` replaces the ledger |

The PENDING ledger cites a step by its number alone: its «step 2» entries are
2a (the hand-joined four) and 2b (the undeclared three and the debrief's
hand-built text), and its «step 4» entries are 4a to 4c. Step 1 removes none of
them.

**Order.** Step 3 is cheap now and costly later: the Cognition session is in
shadow (`session_mode` is `off` in the default config; the live data shows
shadow), and the cut-over (migration step 4) makes its prompt a live one.

## 6. Open

- Row-id `n` (siblings' shape, multi-room windows) over positional ids with a
  `resolve_positions` remap. Recommended; the operator decides.
- `loop_decisions` skips lineage because it is Record-layer and cross-room by
  design (`nora/agent/prefill.py`); whether Record-layer decisions should still
  be gated per room is UNVERIFIED.
- Fire omits `<peer_history>` on every kind; whether that is deliberate is
  UNVERIFIED.
- Honcho's `chat` is a prompt to another agent's model (`nora/agent/tools.py`,
  `nora/app/gatherings.py`); whether "every agent" covers a memory service's
  dialectic query is the operator's call. Not classified in step 0.
