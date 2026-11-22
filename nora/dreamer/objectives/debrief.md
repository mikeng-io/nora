---
name: debrief
kind: objective
version: 1
applies_to: [standing_debrief]
description: Re-judge open intentions using one finished engagement pass.
change_policy: operator-only
---
Re-judge each open intention against the finished pass. Return a JSON object with an
`operations` array, in order. Omit each intention that still holds. Give an empty
array when nothing needs to change. Read room messages for context. Cite only ids
from the pass's recorded items.

Use `open`, `revise`, `drop`, and `close` as the `op`. A revision and a drop name the
open intention's current id in `base`. A drop may cite its evidence. For a close,
name `base` and the pass item id in `witness` that meets its typed closing condition.
The matcher checks that witness. A send attempt or an uncertain outcome proves
no delivery.

An open or revision supplies `form` (achieve or maintain), `condition` with a
`form` and `value`, `owner`, `owed_to`, and `delivery`. Use person ids or `nora` for
the principals. Give `deadline` for an untyped achieve condition and a
deadline-form `drop_when` for an untyped maintain condition. Times are ISO 8601
with offsets. Use `when_it_comes_up` or `choose_moment`. The latter supplies
`trigger` with kind `time`, `at`, and IANA `zone`. Add `citations` for evidence and
`serves` for the open intention a child supports.

A finding the pass learned and did not give becomes a child of the decision it
bears on. Open it with `when_it_comes_up`, cite the result item, and use
`serves` to link the decision. Drop that child when the decision is settled or
the finding is given. Treat the finding as an option, and reserve promises for
commitments.

When this pass delivered a named intention, revise, drop, or propose closing
only that intention. Cite the item explaining any moved trigger. Write only the
commit operations. Never write claims, entities, items, prose, or a reply.
