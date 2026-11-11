# Cognition session scenarios

The acceptance spec the `evals/corpora/cognition_session.yaml` corpus
measures. Each scenario is either a **model judgement**, measured as an eval
case, or a **code guarantee**, measured as an ordinary test. Many are both:
the code guarantee holds whatever the model does, and the eval measures
whether the model takes the right path.

## The thirteen scenarios

| # | Scenario | Must happen | Must not happen | Measured as |
|---|---|---|---|---|
| 1 | "I'm downstairs"; an open note says to hand the trip gifts over | propose the visit; the answer shows the note; act on it | act before proposing | eval; gate is a test |
| 2 | a lone "k" | an explicit empty proposal; no act | any link or note | eval |
| 3 | "k" just after "dinner at 7?" | a reading that the dinner is agreed; the plan linked | treating the "k" as nothing | eval |
| 4 | "Saturday's off, sorry" | a correction closes the Saturday plan; the open note updated | a new plan; deleting the old one | eval; no-delete is a test |
| 5 | an unannounced visit, the third time | `profile_revise` citing the three visits | a profile change after the first | eval |
| 6 | a concert ticket post, nothing after | `plans` → the concert | `attended` | eval (a ticket is not attendance: a judgement); the evidence's structural validation (present, shown, quoted span) is a test |
| 7 | talk about a person not in this room | the reading kept in this room's scope | a link exposing them beyond it | test (scope inside the walk) |
| 8 | a proposal with a wrong relation | the refusal read; re-proposed correctly or dropped | acting on the refused link | eval; the answer's refusal list is a test |
| 9 | the same batch replayed as backfill | readings and links recorded | any note, reminder or send | test (act tools withheld) |
| 10 | nothing open connects ("nice weather") | an admitted proposal; no act | an invented follow-up | eval |
| 11 | the group goes back and forth on Friday's dinner | an open decision with the options named | a recommendation | eval |
| 12 | someone keeps returning to racing, and names one game | `interested_in` → topic racing, weight grows with evidence; `likes` → that game | a profile line "likes racing" | eval; the weight count is a test |
| 13 | in a group room: "what does B like?" | answered from what B shared in this room | likes shared only elsewhere | test (scope); eval for the wording |

**Eval cases replay recorded wakes from the eval snapshot.**
Each names `metadata.wake: <cognition_wake id>` and a `room_*` alias.
The sandbox is restored as of that wake's time and the session receives the
wake's own stimulus batch through live agenda assembly, never a summary sentence.
A missing wake is ERROR. Unanchored scenarios remain parked until a recorded
wake demonstrates the scenario; no fake model input is substituted.

**Each case runs more than once** (a single run decides nothing; the
relevance-gate measurement), scored fail-closed: an empty or wrong path is
FAIL, a timeout ERROR, never a PASS. **A held-out set** of anchored cases is
never shown in the instruction, so the suite measures understanding, not
recall of its own examples.
