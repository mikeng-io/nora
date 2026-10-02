# 0021 · Every model prompt is assembled through context assembly, and a test finds the occasions

- **Status:** proposed (2026-10-02); the operator's rule of 2026-10-01 is built and live as DEC-273
- **Date:** 2026-10-02
- **Replaces:** `tests/architecture/test_the_registry_is_exhaustive.py`'s hand list of five
  occasions as the thing that holds the line (the test stays, as the check that a declared
  table matches the blocks a module builds), and the PENDING ledger, `CEILING` and
  `KNOWN_HAND_BUILT` that the enforcer carried while the tree was moved over.
- **Index:** DEC-273

## Context

Nora sends a prompt to a model from many places: the tag and fire turns, the relevance gate,
five Dreamer passes, the debrief and Standing folds, the reaction picker, the Cognition session,
the Instagram summons, a vision summarizer. `nora.domain.blocks` gives them one way to be built:
typed `ContextBlock`s, a declared `_BLOCKS` table, `assemble` to walk it, and shared renderers
that escape (R-13). The operator's rule, 2026-10-01: every agent assembles through context
assembly, "no exception, or it goes back to the original chaos, everything built differently"
([design/context-assembly/04-every-occasion.md](../../design/context-assembly/04-every-occasion.md) §1).

Read from the tree and from the Cognition trace of 2026-10-01 09:08 (same document, §2 and §3):

- The census meant to hold the line, `test_the_registry_is_exhaustive.py`, read a **hand list of five
  files**. The Cognition session was written after the list, hand-built its whole prompt (no blocks,
  no escaping, an id as `[#7998]` where its own `note_create` demands `[7998]`) and was refused three
  times before a note landed. Nothing failed, because nothing looked. Note-merge, the Instagram summons
  and fire's header paragraphs had arrived the same way.
- Each older architecture test caught one symptom: a hand-written tag pair, a `.body` interpolation, a
  direct record read. A prompt in plain prose that avoids all three passes every one of them.
- The first attempt at an enforcer (#675) found twelve failing occasions only after its own review (nine
  at its first commit) and moved them one step at a time (steps 1 to 5, #676 to #706, each with goldens
  captured from master and a revert that turns a named test red). While it moved them it needed a place
  to say what still failed.

## Decision

1. **Every model prompt is built through context assembly.** A module that sends a prompt declares a
   module-level, non-empty `_BLOCKS` table and walks it with the `assemble` imported from
   `nora.domain.blocks`; it never joins rendered blocks by hand.
2. **The enforcer finds the occasions; nobody lists them.** `tests/architecture/test_every_model_call_is_a_declared_occasion.py`
   reads the source of `nora/` and discovers every module that calls a model by a known door and every
   class or function that defines `inputs`. A violation is a plain failing test.
3. **There is no debt ledger and no exception for an occasion.** The PENDING table, the `CEILING` and
   `KNOWN_HAND_BUILT` were deleted when they were empty (step 6), then the sibling tables that were empty
   but open (`HAND_WRITTEN`, `UNCONVERTED`, `_NOT_THE_FALLBACK`, `_NOT_A_BLOCK_TYPE`), each with a control
   proving its test still fails on a synthetic offender. A hand-built text the net cannot see is fixed, or
   given a purpose-built architecture test (`test_framing_is_one_typed_place.py`,
   `test_withdrawal_reaches_every_prompt.py`, `test_one_quote_resolver.py`), never recorded.
4. **What is not an occasion is classified, with a reason.** A MECHANISM (SDK or transport), a RUNNER
   (runs a prompt a named, declared module builds) and an EXEMPT occasion (an authored prompt plus media)
   are named in the test with a written reason, reviewed in the PR that adds one and re-checked by the test
   (the module exists and still calls a model; a RUNNER's builders exist and are declared; an `imports`
   runner imports one). This is a classification, not a list of tolerated violations; it is also the one gap
   a reviewer can still be argued across, and the design node says so.
5. **A new occasion follows `design/context-assembly/04-every-occasion.md` «How a new occasion joins»**:
   what it declares, which test enforces each part, and what no test checks.

## Consequences

- A new occasion cannot ship hand-built and silent: the first CI run names the module. A module that
  reverts to a hand join, or keeps one `assemble` beside a hand join, fails by name. The enforcer's own
  wiring is held by a test per check and by `scripts/mutations/context-assembly-6-close.json`.
- The rule survives a refactor of what an occasion is, because it keys on behaviour (a call to a model, an
  `inputs` method) and not on a file name somebody remembered to add.
- **Worse**
  - **The net is per module and per call shape.** It does not see a hand-built text inside a RUNNER,
    MECHANISM or EXEMPT module (a cheap f-string rule was tried and found seven non-violations in the fifteen
    classified modules, so it would need exactly the allow-list this decision refuses), a model door it does
    not know (Honcho's `chat`, image generation, a positional `.complete(s, p)`, an aliased call, a bare
    `agent(prompt)`), a call reached only through a helper, or code outside `nora/` (`evals/rebuilt/grading.py`'s
    judge). Review covers these; the lists are in the design node §4 and are not tracked as debt.
  - **A classification entry is a judgement.** One line in MECHANISM, or a `dispatched` RUNNER, turns a
    hand-built occasion green. Only review stops it.
  - **A strict rule has a cost per change.** Adding a prompt means a block type with a description, a table
    row and a golden, even for a small pass; the alternative cost, a prompt nobody can find, is the one this
    ADR exists to avoid.
  - **No ledger means no gradual path.** A future large occasion that cannot be converted in one PR has to
    land converted, behind a flag, or not land. The track needed one for steps 0 to 5 and has none now.
  - **The rule says nothing about quality.** A declared, assembled prompt can still be a bad prompt; the
    open prompt decisions (ages, tool-result transcripts, the `<tag_message>` description) are
    listed in the design node §7 for the operator.

## Rejected

- **A hand list of occasions, as before.** It cannot fail on an occasion nobody added to it, which is
  the failure it was written to prevent.
- **Keep the PENDING ledger as a permanent ratchet.** A ledger that only shrinks is still a place to put a
  violation instead of fixing it, and it reads as policy. It was right as scaffolding and wrong as the end
  state.
- **Enforce at runtime** (refuse to send a prompt not produced by `assemble`). It would need a marker on
  every string and would test what a model reads, not how a prompt is built; the failure it cannot report is
  the one at review time.
- **Exempt Honcho's dialectic `chat` and image generation by name.** Not decided: whether "every agent"
  covers a memory service's own model is the operator's call (design node §7), and a name in an exemption
  table is how the list grows.
