# Decisions

**RESERVED, not claimed.** `DEC-181 .. DEC-185` — DEC-181 is withdrawn, see below. Per AGENTS.md, re-read
`design/09-decision-index.md` at MERGE time and take the numbers then.

The index tops out at **DEC-162**; `ambient-loop` reserved DEC-176..180, and
the #219/#220 session has reserved nothing. **DEC-163..175 is an unexplained
gap** — find its holder before banking on anything in this range.

---

## DEC-181 — the key follows from the writer, and is not a choice

**WITHDRAWN as an operator question.** It was escalated as "which key does
culture use" and it is not a preference. `GLOSSARY.md` now carries the rule
(PR #230), and it is this:

> Which key a store uses follows from **which scheduler hands it the key**. The
> ingest and tag paths get the message that arrived, so they key
> `(platform, conversation_id)` — `messages`, `standing`, `decision_log`. A
> Dreamer adaptor gets `scopes()`, so it keys `primary_id`.

Culture is Dreamer-written. **Therefore `primary_id`.** Nothing to decide.

What made this look like a choice is that `"room"` named four things and the
glossary defined two of them. `nora.policy.Conversation` carries
`ids: frozenset[str]` plus one `primary_id`, so `(platform, conversation_id)`
and `primary_id` are different concepts — which thread arrived, versus which
conversation it belongs to — not two spellings of one.

All six live conversations hold exactly one id, so the two are equal today.
That is an accident of data: give one a second id and a single conversation has
two `standing` rows and two `decision_log` streams against one `scope`, and
nothing reports it because nothing holds both keyings and so nothing compares
them. **Issue #229** carries the load-time guard and the open question of what
a multi-id conversation should mean for the per-room stores.

The original failure mode still stands as the reason to get it right: a store
keyed differently from its loop matches zero rows on the watermark query, so
the adaptor is scheduled, raises nothing, logs nothing and never runs.

---

## DEC-182 — the tier folds its own room's messages into an account

> **NARROWED 2026-09-17, by the operator.** As written below this decision
> reads as *"the tier asks the memory system nothing"*, and that is broader
> than what was decided and broader than what the evidence supports. **What
> DEC-182 killed is the CACHE SHAPE** — ask Honcho once, store the answer, do
> nothing else — **not the use of memory at all.** The tier is a Dreamer
> adaptor, and what a Dreamer adaptor does is **synthesise across the stores
> Nora already has**. The room's batch is ONE input among several, not the
> corpus. Asking memory is back in, through the read verbs the ceiling already
> admits (correction 14), and the surviving clause is the one about
> attribution: an account is produced by asking a model to write prose, so
> Honcho's dialectic endpoint is not the author and its 17–30s latency is not
> on this path. `01-the-adaptor §1` and `§2` carry the input list.

The adaptor reads the batch the Dreamer hands it —
`record.since(conversation_id=key, after_id=watermark)`, `WHERE
conversation_id = :key` in our own Postgres — and its model writes one short
account from it. Nothing is asked of Honcho.

An earlier version of this decision said the opposite: call Honcho's dialectic
`chat` and cache its answer. That was built (#237) and closed unmerged, because
**Honcho does not scope at the layer this needs.** `chat` on a two-message probe
session answers freely about people who appear nowhere in it; `search` scoped to
that session returns hits from other rooms identical to the unscoped query; and
one Honcho workspace sits behind all of Nora's rooms.

The chronicle-versus-account distinction that motivated the Honcho route
(`00-overview §3`) is unaffected and remains the reason this tier exists. What
was wrong was the attribution: an account comes from **asking a model to write
prose**, not from that endpoint. The Dreamer already has a model, a loop, and a
corpus that is genuinely scoped.

This also restores the workspace half: fold one workspace's rooms together and
the boundary is DEC-105's own.

---

## DEC-183 — nothing in this tier retires

No `retired_at`, no `closing_condition`, no TTL, no fail-closed staleness.
`parent_revision_id` means "the same thing, earlier", never "superseded".

Culture is not a claim that expires. A quiet room's culture is accurate, not
stale. The operator's test: 「漢人」 did not retire when the Han dynasty did —
the old name became history, and the history is part of what the new name
means.

The first draft imported `standing`'s lifecycle wholesale and was wrong in
every particular.

---

## DEC-184 — prose body, no section schema

`body` is free text. No JSONB, no required sections, no CHECK on shape beyond
a length cap.

A three-section schema was drafted and rejected: it is the same enumeration
error as `participation.md`'s five shapes one layer down, it could not have
held 「秘書」 — a role the room invented — and requiring a `shifted` section
makes the first revision unwritable or invites a `reasoning_effort = "none"`
model to confabulate a diff to satisfy a constraint.

`note_revision` is the precedent: free prose, CMS-clean, because provenance
sits *around* the text. Here the runtime supplies that provenance itself.

---

## DEC-185 — the watermark is BOTH the gate and the cursor, and the two are sized together

`uses_watermark = True`, `min_interval_seconds = 86400`, **`batch_limit` sized
for a real read**, watermark seeded at the record head on first run.

Culture fires when the room has produced new human messages and a day has
passed. A silent room does not fire, which is correct: nothing retires here, so
there is nothing to go stale and no new evidence to fold. Re-running on silence
buys either a duplicate account or model variance, and the second corrupts the
only signal the revision chain carries.

An earlier version of this decision said the opposite, borrowed from the note
curator's `uses_watermark = False`. The curator needs that because an anchor
date passing is itself the event; culture has no clock event. The precedent
does not transfer, and DEC-183 ("a quiet room's culture is accurate, not
stale") already contradicted it.

**`batch_limit = 1` is withdrawn**, and it is the one clause of this decision
that did not survive DEC-182. It was set on the assumption that the adaptor
asks Honcho and never reads its batch — under that assumption the watermark is
purely a GATE and one row is enough to open it. The adaptor now folds the batch
itself, so **the batch IS the corpus** and a limit of 1 starves the fold:
one message a day is not a room's culture. So the watermark is a gate on
`lag > 0` AND the delimiter of what the fold reads, and the two must be sized
together rather than one being chosen to serve the other.

Head-seeding survives unchanged, and for its original reason: `batch_limit`
caps how far one run advances the watermark, a new adaptor starts at 0, and at
one batch per day the default shape would spend ~17 days on the busiest live
room (1,362 messages) walking a backlog to catch up to a present the room has
long since moved past. Culture wants what a room has been saying lately, not
its origin story replayed a batch a day.

`01-the-adaptor §4` carries the sizing argument in full and is the authority if
these two ever disagree again.

---

## Not decided here

- **The gate metric's `conversation_id` label** — no longer a separate issue
  (#221, closed into this bundle). It is step 1 of `02-measurement §5`, because
  nothing else here can be measured until it lands.
- **Whether the gate ever reads culture.** Owned by the gate, unhelped by
  #220, sequenced last, and with #144/#147 and #216 (n=5, unconfirmed) as evidence against
  assuming it. `02-measurement §5`.
- **The block's rendering.** #219/#220 owns the vocabulary. Its `key` axis
  landed 2026-09-13, so the declaration is now pinned rather than described:
  `placement="turn"`, `key="room"`, per `01-the-adaptor §5`. The rule that
  field encodes — anything keyed narrower than `global` belongs in `turn`
  whatever its cadence, because the shared static head fragments per room —
  belongs to that work, not this bundle.
- **`participation.md` from taxonomy to contrast, and `silence.md`.** Doctrine
  is `change_policy: operator-only`, and this repo's own worked example is
  three prompt rewrites judged by eye, each worse than the last.
- **`standing`'s repair** (#222), which HOLDING's second arm depends on.
- **Why `proactive_decide` declines while holding the notes it woke on.** A
  separate fault; likely #138's `memory before remind`.
- **Dropping `room_portrait` / `room_lexeme` / `room_exchange`.** Per the
  2026-09-07 rule the tables stay when the code goes.
- **Phase 2.** `04-phase-two-note.md`, not executable.

## Corrections this bundle carries

Recorded because they were load-bearing and wrong, and a reader should know
which claims were tested rather than argued.

1. **"The `go` counter has never been incremented."** False — an instant
   Prometheus query read a post-restart absence as never-happened.
   `00-overview §1.1`.
2. **"Honcho has no session scope, so per-room recall is impossible."** False;
   asserted by the first draft and by one reviewer. `session_id` works today
   and returns genuinely room-specific content.
3. **"A third block is forbidden."** A pin (`== 2`) mistaken for a
   prohibition. Every tier that landed added a block.
4. **"`<self_card>` already reaches the participation turn."** False — #210:
   `person_id=None`, so `peer_card` is `()` and `self_card` is assembled only
   on the tag path.
5. **"`strikes` counts wakes."** It counts consecutive revisits that found the
   head unchanged.
6. **A room-keyed block belongs in the cached head because it changes slowly.**
   Backwards: the head is shared across rooms, so a room key fragments it
   permanently.
7. **"Same window + same instruction hash + same model re-derives a compatible
   account" is a usable check.** It is not: `proactive_decide` recall runs
   0.20–0.50 on an identical prompt and DEC-75 records a temp-0 judge scoring
   1/3 on a re-run of a 3/3. The spread is wider than anything it could
   detect. Replaced by `02-measurement §6`, whose third tier — move a labelled
   metric or do not ship into that prompt — is the only one that can falsify
   this tier's value.
8. **"n=20 is the price of the decision check."** Wrong. Power at n=20/arm is
   0.14–0.18 for a 0.20 difference; 80% needs ~86. And the price was quoted for
   5 cases when the check is specified over a suite — 12h for two arms on the
   40-case suite, ~36h at k=3.
9. **"Everything the adaptor needs exists."** Called false, then TRUE again —
   and the round trip cost a built-and-closed PR. The objection was real:
   `ask_memory` is person-scoped and returns `unavailable` forever for a
   scope-keyed adaptor, so the tier specified two new verbs
   (`ask_room_memory` / `ask_workspace_memory`) and they were built. #237 was
   then **closed unmerged**: the path they served does not scope (correction
   12), and with the Honcho route gone the adaptor needs no verb at all — it
   reads the batch the Dreamer already hands it. **No tool registry change.**
   `01-the-adaptor §1.1`.

   > **THE CONCLUSION IN THIS ENTRY IS WITHDRAWN, 2026-09-17 — see correction
   > 14.** «`ask_memory` is person-scoped and returns `unavailable` forever for
   > a scope-keyed adaptor» is false as a statement about the verb. It reported
   > a DEFAULT CONFIGURATION as a property of the tool, and it was the stated
   > reason a built PR was closed. The rest of the entry — that #237's Honcho
   > path does not scope, and that those two new VERBS are not needed — stands
   > untouched; what does not stand is «so the adaptor can hold no tools».
10. **"She has never been admitted and chosen silence."** A `shadow = true`
    artifact — no turn ran for the first ~10 hours of the window.
11. **"`go` = 1."** At least 2. Step-60 grid aliasing stepped over a sample
    shorter than the step. This is the third wrong value for the same counter,
    and §1.1 now carries all three clauses of the rule instead of one.
12. **"The memory system can already produce a room-scoped account."** False,
    and it was this bundle's load-bearing premise for a day. `session_id`
    scopes message retrieval and not conclusions, so the measurement that
    seemed to prove it — correct per-room speaker counts — landed on the one
    question that happens to fall on the scoped side. Falsified by asking a
    two-message probe session about people it has never seen.
13. **The DEC-182 rewrite was applied to three files and not to the other
    three**, 2026-09-13 → corrected 2026-09-16. `_index.yaml`'s title and
    description, `00-overview §0` and `§4.1`, this file's DEC-185
    (`batch_limit = 1`) and correction 9 all still described the withdrawn
    Honcho route — so the bundle's own front door said "asks the memory
    system" while `01-the-adaptor §1.1` said the opposite. **A decision
    reversed late in a session is not landed when the deciding file changes;
    it is landed when every file that quoted the old answer does**, and a
    reader entering through the index would have built the wrong tier. Two of
    the six were not cosmetic: `batch_limit = 1` starves a fold that now reads
    its batch, and correction 9 told an implementer to ship a tool registry
    change that #237 closed unmerged.

    **That rule is the WRITER's half, it is true, and on its own it did not
    work.** Three more sites were missed by the same sweep — `00-overview §5`
    and `§6`, and `01-the-adaptor §7`, all authored in `db71b8a6` (2026-09-13
    02:13, the Honcho-cache draft), untouched by `f856cccf` 19 hours later and
    untouched by this correction's own sweep in `6691daa1`. They are the
    seventh, eighth and ninth, corrected 2026-09-17.

    A rule addressed only to the writer cannot help a reader, and the reader is
    who pays. On 2026-09-17 a careful one read those three un-swept sections,
    believed them, and told the operator the implementation had gone the wrong
    way. It had not; the sections had. So this correction needs its
    **detection** half, and it is one command:

    > **`git blame` a design section before acting on it. If it predates the
    > decision it appears to contradict, it is dead text, not a finding.**

    Cheap, available to anyone, and needing no cooperation from whoever wrote
    the section. It is the same move `00-overview §1.1` makes for Prometheus —
    never infer from an absence without checking the instrument was alive —
    applied to prose instead of to a counter.

14. **"`ask_memory` is person-scoped and returns `unavailable` forever for a
    scope-keyed adaptor."** False, and it is the reason PR #237 was closed. Read
    off the code at `a8cd40db`:

    - `ask_memory` **is** in `ToolRegistry.read_only_tools()` — one of 35
      (`nora/agent/registry.py:533`; the set is derived from
      `NativeTool.read_only`, which `ask_memory` declares at
      `nora/agent/tools.py:680`). It was never outside the Dreamer's ceiling.
    - `nora/agent/tools.py:3228` does `view = await room.scope()` and returns
      `unavailable` only `if not view.available`. **The dependency is a
      resolvable room roster, not personhood.** A verb that answers ABOUT a
      person is not thereby a verb that needs a person as its KEY.
    - `RegistryToolSource` (`nora/agent/dreamer.py:761`) documents both modes,
      and correction 9 observed one of them and generalised it to the verb. By
      default the `Room` handed to `ToolRegistry.build` is EMPTY — no roster
      source, so `available=False` and every room-scoped verb answers
      `unavailable`, deliberately, as a data-level fail-closed guard behind the
      name-level one. That is the state correction 9 saw. **But the same
      docstring carries the other mode**: *"An adaptor whose `AdaptorGrant`
      names a home room is built FROM that room — the real roster source for
      its platform, Nora's own `self_ids` … and `location_trusted` from the
      room's `location` flag — so `where_is` and its siblings run the
      disclosure guard exactly as on a tag."*
    - `AdaptorGrant` (`nora/agent/dreamer.py:387`) is that mechanism. It is not
      hypothetical and it is not new: the proactive adaptor has held one since
      DEC-162. Its docstring also answers the disclosure objection in advance —
      `WORKSPACE_SAFE_TOOLS`'s refusal, *"binding one to an arbitrary room of
      that workspace would be a disclosure decision nobody has made"*, *"is
      answered by the operator naming the room in `policy.toml`."*

    **So `ask_memory` is ungranted, not unreachable**, and those two words are
    a built PR apart. #237 was therefore closed on a false premise. That is a
    claim about the REASON and not about the code: the two Honcho verbs it built
    may well be the wrong shape — correction 12 stands, and the path they served
    genuinely does not scope — but «the adaptor can hold no tools at all» was
    never the finding it was recorded as, and a reader of correction 9 would
    have carried it into the next design unchallenged.

    **Culture's case is simpler than proactive's, and that is the part worth
    stating.** Proactive is unit-keyed, so a unit has no room and one
    must be CHOSEN — hence `home_rooms` in `policy.toml`, and hence a
    disclosure decision for the operator to take. Culture is `key_kind =
    "scope"` (`nora/language/culture.py:145`), so its key already IS the
    conversation and `RegistryToolSource.build` already passes it as
    `conversation_id=key`. **The grant is an identity.** There is nothing to
    choose, and no disclosure beyond the one the tier already has by reading
    that room's messages.

    One thing that is not yet wired, said plainly so that «the mechanism
    exists» is not read as «the wiring exists»: `home_room` resolves through a
    unit — `build` does `home = self._home_rooms.get(unit or "")` —
    and the runtime supplies `unit=key if adaptor.key_kind == "unit"
    else None` (`nora/agent/dreamer.py:1249-1256`). A scope-keyed adaptor
    granted a home room TODAY looks up `home_rooms[""]`, finds none, and takes
    the loud closed branch with room and acts both withheld. What is missing is
    the platform and the roster source for a room the runtime already names: a
    small resolution nobody has written, not a decision nobody has taken.

    The general form of this — that tool access is the precondition for any
    Dreamer adaptor to do more than restate its batch — is not culture's, and
    is recorded where it belongs, in
    `design/dreamer-agent-loop/03-no-agent-loop §7`.
