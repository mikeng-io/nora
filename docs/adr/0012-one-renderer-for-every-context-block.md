# 0012 — Every context block is built by one renderer, and the transcript stops being lines

- **Status:** accepted
- **Date:** 2026-09-13
- **Index:** none — this changes no rule about what Nora may do. It changes how
  the text she is shown is BUILT, and it strengthens two existing rules on the
  way past: R-13c (a row may not forge a row) now holds by construction rather
  than by nine renderers remembering, and R-27's fail-closed distinction is
  applied to the prompt's own catalogue. Pinned by
  `tests/unit/test_blocks.py`, `tests/architecture/test_one_row_renders_one_line.py`
  and `tests/architecture/test_escaping_is_for_prompts_only.py`. Issues #219, #220.

## Context

Nora's prompts are assembled from labelled blocks — `<group_transcript>`,
`<peer_card>`, `<notes>`, `<standing>` and five more. Before this change, every
one of them was built by hand, in the module that happened to own its data, as
an f-string:

- Nine functions across five packages turned a record into a line, each one
  deciding for itself whether to escape, whether to collapse a newline, where
  the handle went and whether the speaker was bracketed.
- Eight of those nine did not collapse. The one that did
  (`ingest/relevance_gate.py`) had gained a private `_one_line` on 2026-09-10
  with a live measurement behind it, and nothing asked whether the other eight
  had the same hole. They did, including the tag prefill — the one a member
  actually talks to.
- The catalogue at the top of the prompt («What you can see:») was a
  hand-written list beside the blocks it described. It went stale five times.
  Twice a block shipped on every turn with no entry at all
  (`<loop_decisions>`, then `<people_in_window>`).
- Two blocks put their own attribute before `untrusted=`, two after, and
  `<peer_card>` carried neither for months.

The operator-visible symptom was the one that started this: a member pressing
enter mid-message produced a continuation line with no handle, no age and no
name, immediately above the next real row — so «Grant: I will go / to school
by bus» read to the model as a fragment belonging to whoever was adjacent.
Reported as «Blake: go to school by bus». It needed no attacker.

## Decision

**One renderer per shape, and a block becomes text exactly one way.**

1. `nora/domain/blocks.py::render_rows` renders a record as a LINE. A line
   grammar is a `RowStyle` — four fields of plain data — plus accessors the
   caller passes. The collapse and the speaker-delimiter strip are applied by
   the renderer and no style turns them off.
2. `render_elements` renders a record as one ELEMENT: the identifying fields
   become ATTRIBUTES, where `escape_attr` closes the quote and every value is
   collapsed, and the record's own prose becomes CONTENT, where it keeps its
   shape. `render_messages` is a thin caller of it with the transcript's
   attribute set (`n`/`age`/`from`/`replying_to`). A body then keeps its
   paragraph breaks, because the separator is a tag rather than a newline —
   the rule this repo had already written down in `relevance_gate`'s docstring
   and applied to nothing: *the separator has to be something a row's content
   cannot contain*. ChatML's special tokens, LangChain's message objects and
   Anthropic's `<document index="n">` are three stacks making the same choice.

   **Amended by #292.** This ADR was written when the element form had exactly
   one caller and eight line grammars remained. Every builder that renders a
   MESSAGE now renders elements — the relevance gate, the culture fold, the
   note curator's evidence and own-sends, the note-candidate batch, the debrief
   fold, and the tag prefill — and so does the note index. Three line grammars
   survive, and none of them is a transcript: the proactive room listing and
   the operator's two Discord surfaces, which are rows addressed to a human or
   describing something other than a message. `render_rows` is now the
   exception rather than the default, and `tests/architecture/test_one_transcript_renderer.py`
   is what keeps it that way.
3. `render_bullets` renders a record as one bullet, for the memory blocks that
   list conclusions.
4. `ContextBlock.render()` is how a block becomes text. There is no `text`
   field and no second path, so `untrusted="true"`, the `note=` framing and the
   attribute order are properties of the type rather than things each renderer
   remembered to type.
5. `prefill._BLOCKS` is one table of `(name, order, key, catalogue line)`. The
   prompt's block order and its catalogue both derive from it, so a block
   cannot be served without an entry and an entry cannot outlive its block.

**Escaping is bounded rather than absolute.** `escape` makes a line legal
inside XML; it is not a property of a line. Two callers in `nora/app/fire.py`
post to the operator's Discord review channel and pass `markup=False`, because
`&amp;` in front of a person and a broken URL are damage, not safety. The
collapse and the delimiter strip still apply there.
`tests/architecture/test_escaping_is_for_prompts_only.py` discovers the call
sites and requires a written reason for each.

## Consequences

- `send_reply(reply_to=n)` now takes the `n` of an `<m>` element. The old `#n`
  notation is gone from the prompt and from the tool's model-facing docstring.
- A reply points at its parent by index instead of carrying a 14-character
  quote of it: unambiguous where the snippet was not, and ~3 tokens cheaper per
  reply.
- One quote resolver (`prefill._by_ts`) serves both `<replying_to>` and
  `replying_to="n"`. They used to disagree on duplicate timestamps — first-wins
  against last-wins — so one prompt could name two different parents for one
  reply. A parent that resolves forward in the window now resolves to nothing.
- `BlockSource`, a Protocol declared ahead of this work, is deleted: the
  assembler landed and never needed it, and its own docstring prescribed
  exactly that. `Placement`, a two-valued Literal with one live value, went
  with it; `BlockKey` carries the cache argument alone.
- **Not measured.** The structural argument is what this rests on, plus the
  gate. `<tag_message>` and `<replying_to>` are still hand-built and still
  carry no `untrusted` marker — marking the message she was asked to answer is
  a change to what she is TOLD, and that needs the eval suites. Issue #239.
