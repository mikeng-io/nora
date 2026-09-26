# 0018 · A note revision restates the body

- **Status:** accepted (2026-09-26; the operator shipped it as #583)
- **Date:** 2026-09-26
- **Reverses, in part:** the append rule the note tier's update arm has
  followed since #228: an update was appended to the head, so a wrong
  `updates` cost a conflation and never an erasure.
- **Index:** DEC-213.

## Context

Design 42 (`design/dreamer-agent-loop/42-note-format.md`) gives a note body a
schema: fixed sections (Decided, Open, Owed, Proposed, Ruled out), a line
grammar, and a citation on every line to the messages the writer was shown.
Appending to a schema body breaks it: the head stops being one note in the
sections and becomes a stack of fragments that no section check can read.
Measured before the change, open notes' bodies had a median of about 200
characters against an 8000 ceiling, and every update failure in the eval
revised a similarly titled note the model had never read (design 42 §1).

#228 chose append because a restatement can erase: a wrong `updates` that
replaces a body destroys what the note said. That risk does not go away. It
has to be guarded some other way.

## Decision

An update restates the whole body in the schema (design 42 S4a). Three
guards replace what append guaranteed:

1. **History is kept.** Every earlier head stays a revision row; nothing
   is overwritten in place.
2. **Evidence is carried forward.** A restatement that leaves any line of
   the head with none of its citations is not written. It is left pending as
   `dropped_citation` (`nora.domain.note_body.dropped_citations`).
3. **An update must be news** (#590). A restatement whose cited messages,
   among those the pass was shown, all predate the head's last change is
   refused as `no_new_evidence`.

The model is shown every open note WITH its body (S3), so it restates what
it has read, not a title. A legacy free-text head is restated from what the
batch shows. Its uncitable lines are not carried; they stay in the history
(objective v10).

## Consequences

**Better**
- A note reads as one current statement in fixed sections, which Nora, the
  curator, note-merge and the proactive adapter all read directly.
- Invented notes fell in the stack eval (negatives 43/52 → 54/56 correct).
- Every line cites its source, so a reader can check a claim against the
  message it came from.

**Worse**
- A wrong `updates` now rewrites the wrong note's head. It is visible in
  the revision chain and recoverable, but it is no longer a harmless
  append. The carry-forward and new-evidence guards narrow this; they do not
  close it. A wrong-note update that cites the batch's new messages still
  lands (the p11 eval case: 2/8 draws also touched a sibling note).
- A legacy note's first restatement can drop text the batch cannot cite.
  The text survives only in the history, and no reader looks there by
  default.
- Longer outputs: about 15k output tokens per call on the stack, against
  9k before. `[dreamer] max_tokens` was raised from 18000 to 28000 to stop
  `PartialReplyError`.

## Rejected

- **Keep append and validate only the appended fragment.** The head still
  stops being one note in the sections, and every reader would have to
  merge fragments.
- **Restate, but let code repair a malformed body.** Code would then be
  authoring the note (design 42 G1): refused, not repaired.
