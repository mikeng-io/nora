# 0004 — A tool name is measured, not argued

- **Status:** accepted
- **Date:** 2026-08-06
- **Index:** DEC-39 (`recall` → `about`, and the name was measured), DEC-29
  (the doctrine loader's structural tool-name gate), R-21 (the three
  room-scoped tools), ADR 0001 (whose last follow-on item this closes)

## Context

`recall` was misnamed: it fetched a standing profile from Honcho, not memories,
and its docstring's «what they told you before» invited Nora to repeat a
transcript back to the room that had just watched it arrive — which ADR 0001's
operative test ("could she have read this in the buffer?") calls negative
value. So it had to be renamed. The question was to what.

**`person_profile` was chosen on a structural argument.** It reads better than
either alternative, and it is snake_case, so `nora.doctrine.loader`'s
structural detector (DEC-29) would catch it for free instead of it needing a
place on that module's hand-kept `tool_names` list beside `now`. A tool whose
name a gate can see without being told about it is strictly better than one
that needs maintenance. That argument is correct, and it lost.

**The measurement, 2026-08-06.** Three candidate names for the same tool, the
docstring held **byte-identical**, only the identifier changed. Against real
`build_tool_specs` schemas, the real assembled doctrine as system prompt,
`tool_choice="required"`, `opencode-zen/deepseek-v4-flash`, n=16, on a question
the tool exists to answer (「我平時鍾意食咩？」 — the asker is named in the tag
message, so `lookup_person` has nothing to resolve). First-tool selection:

    recall           15/16
    about            16/16
    person_profile    0/16

Nothing else changed between those three runs.

`person_profile` also dragged an *unrelated* case down: `read_buffer` on 「what
did he say earlier」 fell from **14/24 to 3/16** (z ≈ 2.8). A name can cost a
different tool its calls.

**The mechanism.** `person_profile` shares the token `person` with
`lookup_person`, and person-shaped routing collapsed entirely onto that tool.
**A tool name is not documentation; it is the feature the router matches on**,
and a name that overlaps a sibling's is a name that competes with it.

**The measurement had to be redesigned before it said anything.** The first
four cases were saturated by `lookup_person` (16/16 in most), because the
harness reads only the FIRST tool call of one completion and cannot see a
two-step plan («work out who Tyler is, then look him up»). A question naming
nobody ambiguous is what made the difference visible; the earlier cases are
reported and carry no signal.

**The docstring rewrite was measured separately, and it also mattered.** A
first draft replaced «what they like» with «what they are into» and led with a
paragraph steering to `read_buffer`: 11/16, where the name alone scored 16/16.
Restoring the lexical overlap with the question and shortening the steer to one
clause recovered 16/16 with the anti-episodic correction intact. Two
iterations, not tuned further — n=16 on one model is not a budget for fitting
prose.

## Decision

**A tool rename is decided by measurement, not by which name reads better —
and renames happen one at a time.**

`about` shipped (DEC-39). The numbers live in `nora/agent/tools.py` beside the
name and in DEC-39; they are **not a test** — they need a live gateway and cost
40 model calls a run.

**Two known-misleading names are deliberately NOT renamed yet:**

- **`read_buffer`.** Nothing is buffered. It reads durable rows from disk;
  GLOSSARY calls it misnamed and proposes `chat_earlier`. It stays because the
  `earlier` case above is precisely a `read_buffer`-vs-`about` decision —
  renaming both at once makes the measurement unreadable in both directions.

> **Update 2026-08-08:** `chat_earlier` was later renamed to `load_transcript`
> (operator, for consistency with the memory verbs `about`/`ask_memory`). The
> history above is preserved as the measurement it records; the tool is now
> `load_transcript`. A routing eval should confirm the new name routes as well —
> this ADR's whole point is that the name is a measured feature, so the rename
> carries the same re-measure obligation.
- **`group_members`.** `Room`/`RoomView` (`nora/agent/room.py`) is the object
  that backs it and the concept the whole R-21 scoping rule is written in terms
  of; the tool's name says "group", which is a platform word for a Signal
  artefact, not the scope Nora reasons about. Same rule: one at a time.

**`lookup_person` → `person_lookup` is now suspect, not obvious.** It was the
tidy-up nobody would have argued with. It is **exactly the shape that just
scored zero** — a `person_*` name in a set that already contains one
person-shaped tool. The `person_*` family is under suspicion until measured,
including GLOSSARY's standing rule that tools answering *"who is 阿豹?"* are
named `person_*` rather than `identity_*`: that rule was written to resolve a
naming collision between three meanings of "identity", and it is a good rule
for readers, but it is not evidence about routing.

**Two names are deliberately left alone, and this is not an oversight:**

- **`send_reply`** — the only terminal tool. It has no sibling to compete with,
  it is the tool a turn exists to reach, and it carries a second meaning the
  name is doing real work for: its budget of 1 *is* the message cap (DEC-32).
  Renaming it would mean touching `ReplyGuard`, R-2, R-7 and the terminality
  property at the same time as a measurement, which is the confound this ADR
  exists to avoid.
- **`now`** — an ordinary English word rather than a snake_case identifier, so
  it is one of the two names `nora.doctrine.loader` must be *told* about
  (`tool_names`), which is the exact maintenance cost `person_profile`'s
  structural argument was trying to avoid. It stays anyway: it is unambiguous,
  it has no sibling, and it is already selected reliably. **Paying the gate's
  maintenance cost for a name that works is cheaper than a rename that might
  not.** That is the same trade `about` made — `about` is also on that list.

## Consequences

**Better.** The rename is settled with evidence rather than taste, and the
evidence is recorded where the name is. The general lesson — a name is a
routing feature, and an overlapping token is competition — is now stated
somewhere other than a commit message, which is the entire reason this ADR
exists.

**Worse, and accepted.**

- **Two tools stay knowingly misnamed**, and GLOSSARY says so publicly. Every
  reader of `read_buffer` has to be told that nothing is buffered. That is a
  standing comprehension cost paid to keep future measurements readable.
- **`about` costs maintenance that `person_profile` would not have.** It is an
  ordinary English word, so `nora.doctrine.loader` keeps it on the hand-kept
  `tool_names` list, and `tests/test_prefill.py::test_the_header_names_no_tool_at_all`
  had to grow a second detector — `_context_header` legitimately contains «what
  you durably remember ABOUT the person who called you», so a raw substring
  match no longer works. That test now uses substring for names no sentence
  contains and catalogue shape (`- <name>:`) for the two that are ordinary
  words. Narrowed deliberately and stated, rather than contorting generated
  prose around a test. **This is a real, permanent tax the rejected name would
  not have levied.**
- **The measurement is not a gate.** It needs a live gateway and 40 model calls,
  so it cannot run in CI. Nothing stops the next rename from being argued
  instead of measured except this document.
- **n=16 on one model is thin.** `deepseek-v4-flash` is the only model tested;
  a 0/16 is unambiguous but a 15/16-vs-16/16 is not, and no claim is made that
  `about` beats `recall`. The finding that survives is `person_profile`'s
  collapse and its collateral damage to `read_buffer`, both of which are large
  enough not to be noise.
- **Renaming one at a time is slow.** `read_buffer`, `group_members` and
  possibly `lookup_person` are three separate measurement runs, each costing a
  live gateway, each blocking on the previous one. The alternative — rename
  them together — would produce a result nobody can attribute, which is worse.
- **`nora_recall_total` keeps the old word.** Metric names are append-only in
  this repo (`nora.obs.metric_specs`' own docstring, R-17's argument):
  renaming does not move history, it starts an empty series beside a frozen
  one. So the metric and the tool now disagree, permanently, and a reader has
  to know why. The `tool` LABEL on `nora_room_lookups_total` DID move, because
  that value must agree with what `_log_call` writes to `data/nora.log` or the
  line stops being greppable by the tool's name — which is the entire reason
  DEC-28 added those lines.

## Rejected

**`person_profile`.** Better-reading, structurally cheaper, and it scored
**0/16** on the question the tool exists to answer while also taking
`read_buffer` from 14/24 to 3/16. Recorded here in full because it is the kind
of name someone will propose again on the same correct-sounding argument.

**Renaming `read_buffer` and `about` in the same change.** It would have made
the `earlier` case — the one that produced the collateral-damage finding —
uninterpretable.

**Treating "it reads better" as sufficient.** It is not evidence about the
router. It is evidence about the reader, and the reader is not the one choosing
the tool.

**Deriving the doctrine's capability block from the schemas to avoid the
`tool_names` list entirely.** Considered and rejected in DEC-29 for an
unrelated reason (every native tool ships `description=""` and derives its text
from the callable, so generation would emit a bare name list and lose *when to
reach for it*). It is noted here only so it is not re-proposed as the fix for
`about`'s maintenance cost.
