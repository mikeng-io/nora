---
type: analysis
title: "There Is No Agent Loop"
description: "Four of five hold no tools; three make a single completion call and the fourth makes none. The fifth has a real loop and can only look outward. The runtime's guards, ceilings and stopping condition all exist and guard a loop that never runs. Re-taken 2026-09-17 (section 7): the participants are all different, one of four now has a real loop, and the finding moves — the agent loop is an implementation of a RESEARCH loop, and an objective that restates its own prompt has wasted it."
tags: [nora, dreamer, agent, loop, tools, diagnosis]
timestamp: '2026-08-22'
edges:
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
  - {dst: 17-failure-classes, rel: relates_to, provenance: asserted}
  - {dst: 39-proactive-ooda-loop, rel: relates_to, provenance: asserted}
---

# There Is No Agent Loop

## 1. The mechanism

`FactCandidateAdaptor` declares an empty tool set. The tool source intersects
the declared names with the read-only ceiling, finds nothing, and returns an
empty list. The agent is constructed with no tools.

The Dreamer's stopping condition is documented as *"the model declines to call
anything"*, under `tool_choice="auto"`. Give it nothing it **could** call and
that condition is satisfied on the first pass.

> The loop does not fail to terminate. It terminates immediately. There is no
> loop — it is one completion with a JSON instruction attached.

This matters for diagnosis, because the natural reading of the production
failure is backwards. The 4,096 output tokens were not burned across a
runaway multi-turn loop; the Langfuse trace carries exactly **one** generation
observation. They were burned inside a single completion. A plain
`chat.completions.create` with the same prompt would fail identically — and
more quietly, returning a truncated body instead of raising.

**Corollary: the SDK shape is not the defect and changing it is not the fix.**
The causes are the prompt, the input bound, and the absence of anything to
look at.

## 2. What is guarding nothing

Every piece of loop machinery is built, correct, and inert for this adaptor:

| mechanism | purpose | state for a tool-less adaptor |
|---|---|---|
| `DreamGuard` R-7 default-deny | refuse an ungranted tool | budget map is empty; vetoes everything by omission |
| the read-only ceiling | a background loop may look, not act | intersects to nothing |
| `WORKSPACE_SAFE_TOOLS` | narrow a workspace key further | never reached |
| `tool_choice="auto"` | *"a loop that is permitted to stop is a loop that can stop"* | nothing to stop calling |
| per-tool budgets via the call ledger | bound tool spend | no tools to bound |
| `turns` | bound loop length | never reaches turn 2 |

The runtime is not wrong. It is a correct implementation of a research agent,
wired to adaptors that give it nothing to research with.

## 3. The loop that does exist looks only outward

`FactAdaptor` holds `web_search`, `web_fetch`, `now`, `calculate`. That is a
genuine loop — it can search, read, and search differently.

It cannot read the room, the transcript, Nora's memory of the people in it, the
notes, or its own prior conclusions. Everything it knows about *itself* arrives
as a string that Python rendered **before the loop started**, and it can never
ask for more, ask differently, or ask again.

Its rendered input carries pending claims and standing facts as bare triples
with source URLs. No transcript. No speaker. No room. No language.

## 4. The consequence, stated precisely

The extraction objective **requires** the model to emit the room's private
vocabulary:

> *"Write the subject in the words the room actually used. If they call it
> 「big walk」, the subject is `big walk` — not your guess at what it is really
> called, and not a translation."*

The adjudicator is then denied every tool that could tell it what the room
means by that vocabulary.

> **The `big walk` failure is reconstructed inside the machine built to fix
> it.**

## 5. Participant inventory **(2026-08-22; four of these five no longer exist)**

> **Superseded as an inventory by §7, 2026-09-17.** The knowledge tier and the
> language tier were deleted 2026-09-07 (issue #115), taking `LexemeObserver`,
> `ExchangePatternObserver`, `RoomPortraitSynthesizer`, `FactCandidateAdaptor` and
> `FactAdaptor` with them. The table is kept because §3–§4 and §6 argue from it and
> the reasoning is the point; **it describes no code that runs.**


| participant | on the runtime | tools | real loop |
|---|---|---|---|
| ~~`LexemeObserver`~~ **(DEPRECATED 2026-09-07, deleted)** | no — legacy one-shot | none | no |
| `ExchangePatternObserver` | no — legacy one-shot | none | no |
| `RoomPortraitSynthesizer` | no — on the clock, not the runtime | none, and **no model call at all** | n/a, correct |
| `FactCandidateAdaptor` | yes | none | no |
| `FactAdaptor` | yes | web only | partial — outward only |

Four of five have no loop; the portrait is the one that should not. The portrait is the one legitimate case: its
objective is executed in code and the keystone argues, correctly, that a third
model call synthesising prose from already-vetted output would reintroduce the
risk the tier removed. Making everything an agent is not the goal.

## 6. What a real loop needs to be able to do

Taking the room's own messages as the worked example — short fragments, links,
mixed script, an @-mention, one long game clue — a loop that could actually
comprehend them would:

- resolve a pronoun by reading around it in the transcript;
- find out who the participants are, and who an @-mention names;
- discover what an unfamiliar proper noun refers to by opening a link somebody
  pasted;
- check whether the tier already knows this thing, and under what name;
- check whether it already tried and failed to settle it.

Not one of those is possible today. The first three are room reads
([10-room-access.md](10-room-access.md)); the last two are reads of the tier's
own state ([08-state.md](08-state.md),
[08-state.md](08-state.md)).

The output of such a loop over that batch is not sixty-five triples. It is two
or three grounded, attributed claims and a decision that the rest was chatter.

## 7. Re-taken 2026-09-17 — the loop runs now, and it is still not a research loop

Everything above was written on 2026-08-22 against a participant list that no
longer exists. The knowledge tier and the language tier were deleted on
2026-09-07 (issue #115), which removed four of §5's five rows; the note tier,
the proactive adaptor and the culture tier arrived after it. **§5 is a record
of the reasoning and is not an inventory of anything running.** This is the
inventory, read off the tree at `a8cd40db`.

| adaptor | declared | `key_kind` | `tools` | real loop |
|---|---|---|---|---|
| `NoteCandidateAdaptor` | `nora/language/note_candidates.py:845-855` | `scope` | **none** | no |
| `NoteCuratorAdaptor` | `nora/language/note_curator.py:576-585` | `workspace` | **none** | no |
| `CultureAdaptor` | `nora/language/culture.py:365-390` | `scope` | 9, `CULTURE_TOOLS` | **yes** |
| `ProactiveAdaptor` | `nora/language/proactive.py:410-415` | `workspace` | 16, `PROACTIVE_TOOLS` | **yes** |

> **The culture row moved later the same day** and the rest of this section is
> left as written, because what changed is exactly what §7.4 and §7.5 asked
> for. `CultureAdaptor.tools` was `frozenset()` when the table was first
> compiled and is nine read verbs since — `ask_memory`, `search_memory`,
> `about`, `lookup_person`, `group_members`, `where_been`, `online_now`,
> `web_search`, `web_fetch` — reachable through an
> `AdaptorGrant(home_room=True)` (`culture_grant`), and its `inputs` now render
> the room block, the previous account, `standing` and the note index before
> the batch. **Its objective text did not move**, which is the live instance of
> [13](13-doctrine.md) §8.3: the grant landed and the sentence that would spend
> it did not.

Two of four are one completion with a JSON instruction attached — §1's shape,
arrived at independently a second time, by tiers designed after the node that
named it. The runtime is not the reason and never was:
`ToolRegistry.read_only_tools()` (`nora/agent/registry.py:533`) resolves to
**35 verbs** today (re-measured 2026-09-17: 35 of 56 `NATIVE_TOOLS` rows carry
`read_only=True`), and two adaptors ask for none of them.

**The count is the old finding. The new one is that the count was never the
point**, and this node's title is a symptom of reading it that way: an adaptor
with tools and nothing to do with them is no better off than an adaptor with
none.

### 7.1 The agent loop is an implementation; the thing it implements is a research loop

The operator's statement, 2026-09-17, and it is the frame this whole bundle has
been missing:

> 喺 Dreamer runtime 入面，agent loop 其實只係實現 Dreamer 嘅一個
> implementation。最終真正發生嘅 metaphor 係 business side 嘅 research loop，
> 佢哋要去 research、think、思考個答案出嚟。而唔係單純話「阿 Miles 鍾意食宵夜，
> 所以阿 Miles 鍾意食宵夜」，呢啲係冇用嘅 — 呢啲資料直接喺 store 度就已經 derive
> 到出嚟。Dreamer 最重要嘅用途係喺好多唔同嘅 data store 入面 derive 返有用嘅
> information 出嚟，而唔係單純攞 raw data 出嚟。

The tool ceiling, the budgets, `tool_choice="auto"`, the turn cap — §2's whole
table — are the machinery of an agent loop. None of them is the *purpose*. The
purpose is that a background pass goes and finds out something nobody handed
it, and the test of whether it did is not how many tools it called but whether
its answer could have been written from its own prompt.

**An answer that could have been written from its own prompt has wasted the
loop, and the tautology it produces looks exactly like a finding.** «Miles likes
late-night food, therefore Miles likes late-night food» is true, well-formed,
specific and grounded in the batch. It passes `accept`'s shape check, it passes
R-18/R-28 screening, it increments an `outcome="ok"` counter, and nothing
anywhere in the pipeline can distinguish it from a derived one. That is the
measurement hole this node should have named in August and did not: §2 lists
six mechanisms guarding a loop that never ran, and not one of them would fire
on a loop that ran and restated its input.

### 7.2 The worked example — three stores, and no one of them holds the answer

The operator's, and it is the clearest statement of what derivation means here:

> The transcript shows two members talking about going for 宵夜. That is all a
> message history can tell you. But `where_been` — presence analytics — shows
> that three of them were actually co-located that Saturday, so it was not two
> people, it was the group. And `online_now` shows a fourth was at home gaming
> instead. The culture is not "those two eat late"; it is "this group goes out
> on Saturdays, and that one is the one who stays home."

**No single store holds that. Three stores cross-referenced do.** The messages
carry the intent, presence carries who was actually there, and live state
carries who was elsewhere — and the conclusion is a property of the *join*,
which is exactly the shape a query cannot express and a model with three read
verbs can.

Note what the example does NOT need: a bigger batch, a better prompt, or more
reasoning effort. It needs two reads the pass was never given.

### 7.3 What the four objectives actually ask for — checked, not assumed

The operator's claim was that the note tier and the proactive tier have agent
loops and do not use them to think. **Checked against the four objective texts
on 2026-09-17: right about three, wrong about the fourth, and the fourth is the
more interesting reading.**

**`note-candidates.md` — restate and classify.** Its own frontmatter closes the
question:

> *"Deliberately tool-free: you propose, code decides whether the note you
> named exists."*

Its output contract is a per-message verdict — *"`verdicts` holds ONE entry per
message in the batch, in the order you were given them — no message left out,
including the ones that are obviously nothing"* — over three booleans-ish
fields (`ahead`, `committed`, `open`). Every one of those is a property of the
message the model was just handed. There is no question it can ask about
anything it was not handed, and the objective never suggests there is one. It
is a classifier on a schedule.

**`note-curation.md` — adjudicate over handed evidence, and only handed
evidence.** The job:

> *"Your job is to say, for each note you are shown, whether that is still true
> — reading its body AND whatever the room has said since, **which is shown to
> you as that note's evidence**."*

The clause after the dash is the whole finding. And the citation rule closes
the loop deliberately: *"A number you cannot point at in the evidence you were
given is not a citation, and the entry will be refused."* That refusal is
correct on its own terms — it is §3's «never mint a value» applied to a message
id — but it also fixes the pass's universe at what code selected in advance.
`nora/language/note_curator.py:582` says so outright: the adaptor *"has nothing
to look up"*.

**`culture.md` — asks for derivation, over one store.** This is the near miss.
Its instruction is the most explicitly anti-tautological text in the corpus:

> *"A record says what happened. «On the 12th they argued about the
> restaurant.» An account says what this is, and why it became so."*

> *"If a sentence you have written could be swapped for a different date's
> sentence without changing the meaning, it was a record and you should cut
> it."*

That is the research-loop test written out in full, one level down. But when
this was written the adaptor holding it declared `tools = frozenset()` and its
`inputs` rendered exactly two things — the previous account and the batch — so
the only material it could derive across was one room's messages and its own
last answer. It was asked for a join and handed one table. The tier's own
design bundle carried the sharpest instance:
`design/culture-tier/01-the-adaptor §2` asks the model for *"my own standing
here"* while the `standing` tier holds precisely that, per room, and the
adaptor read it not at all.

**Closed the same day, on the input side only.** `CultureAdaptor.tools` is now
`CULTURE_TOOLS` (nine verbs, `culture.py:365-390`) and `inputs` renders
`standing` and the note index alongside the previous account and the batch
(`culture.py:454`). The join is reachable. **What did not change is the
objective**, which still nowhere says what the pass must go and find out — and
under the doctrine above it did not need to, because the preamble answered that
question for it with «answer from the text you were handed». That is
[13](13-doctrine.md) §8's finding, met here first.

**`proactive.md` — a genuine research loop, and the operator's claim does not
hold against it.** Reported as found rather than as expected:

> *"# Before you set any reminder: ask what you know … A note tells you what
> the group has not settled. It does not tell you who these people are — what
> they usually do, what they said last time, what they care about — and that
> is exactly what decides whether a nudge helps and when. You hold that
> knowledge; it is in your memory, not in the note. So, **before every
> `remind`, ask memory first**."*

And the acceptance test for it, which is this section's own thesis stated in
the corpus a week before this section existed:

> *"A `why` that could have been written from the note alone is a sign you
> skipped this."*

That text is not aspirational: version 2 of the objective records that it was
written *because* of a measured failure of exactly this class — *"the first
live run set a reminder from the note body alone, without asking memory about
anyone involved — so a reminder now REQUIRES a memory read first, and the
decide suite fails one made without it."* An objective, a suite case, and a
refusal. So the proactive tier is the corpus's one worked precedent for making
a loop research, and the general finding should be argued **from** it rather
than against it.

**The narrower claim survives even there, and it is the one worth acting on.**
Every read `proactive.md` asks for is a *lookup keyed by something the note
already named* — `note_read` for the body, `about` and `lookup_person` for the
people it names, `ask_memory` for one question about them,
`search_memory_workspace` for something the body alludes to. That is depth on a
known key, not a cross-reference. The one verb in the grant that crosses stores
is `online_now`, and `nora/language/proactive.py:153-161` records why it was
granted on 2026-09-15: a note saying a member was unwell and wanted the evening
off was raised into the home room while the group was mid-game, *"because
nothing in the turn could observe the state contradicting the note."* **That is
the 宵夜 example's structure exactly, found in production, one store short.**
`where_been` — the other half of the example, and in `read_only_tools()` —
was in no adaptor's grant at all when this was written. It is in
`CULTURE_TOOLS` since 2026-09-17, and in no other adaptor's; the proactive
pass is still the verb short.

### 7.4 Tool access is the precondition, and `AdaptorGrant` is how an adaptor gets it

A research loop is not reachable by rewriting an objective. When this was
written, three of the four adaptors could have been handed the best-written
instruction in the corpus and would still have had nothing to call, and the
fourth would still have been unable to ask where anybody has been. Two of four
today — the culture grant closed one of them, and §7.5's coda records what it
did not close.

The mechanism exists and is not new. `AdaptorGrant` (`nora/agent/dreamer.py:387`)
is what an adaptor holds past the Dreamer's defaults, and `RegistryToolSource`
(`:761`) documents both of the states an adaptor can be in:

> *"**An empty room.** The `Room` handed to `ToolRegistry.build` has NO roster
> source, so its view is `available=False` — «I could not look» — and any
> room-scoped tool that did slip through discloses nothing rather than
> answering from a room the Dreamer was never given."*

> *"An adaptor whose `AdaptorGrant` names a home room is built FROM that room —
> the real roster source for its platform, Nora's own `self_ids` …, and
> `location_trusted` from the room's `location` flag — so `where_is` and its
> siblings run the disclosure guard exactly as on a tag."*

**So a room-scoped read verb in a background pass is ungranted, not
unreachable**, and the distinction is load-bearing enough that it cost a built
PR elsewhere: `design/culture-tier/03-decisions` correction 14 records a
conclusion — *"`ask_memory` is person-scoped and returns `unavailable` forever
for a scope-keyed adaptor"* — which read a default configuration as a property
of the verb, and closed #237 on it. `ask_memory` is one of the 35
(`registry.py:533`), and `tools.py:3228` returns `unavailable` only
`if not view.available`: the dependency is a resolvable roster, not personhood.

The disclosure objection is answered in the same docstring rather than by this
node: `WORKSPACE_SAFE_TOOLS`'s refusal — *"binding one to an arbitrary room of
that workspace would be a disclosure decision nobody has made"* — *"is answered
by the operator naming the room in `policy.toml`"* (`dreamer.py:387`).

**One thing the mechanism does not do yet, stated so nobody reads «it exists»
as «it is wired».** `home_room` resolves through a workspace:
`RegistryToolSource.build` does `home = self._home_rooms.get(workspace or "")`,
and the runtime supplies `workspace=key if adaptor.key_kind == "workspace" else
None` (`dreamer.py:1249-1256`). A **scope**-keyed adaptor therefore looks up
`home_rooms[""]`, finds nothing, and takes the loud closed branch — room and
acts both withheld. For a scope-keyed adaptor the grant is conceptually an
identity, because its key already IS the conversation and `build` already
passes `conversation_id=key`; what is missing is the platform and the roster
source, which is a small resolution nobody has written, not a decision nobody
has taken.

> **Written 2026-09-17. ADDRESSED the same day**, and the paragraph is kept
> because the reasoning is what the fix implemented. `RegistryToolSource.build`
> now resolves the grant through two maps, one per `key_kind`
> (`nora/agent/dreamer.py:842-855`): a workspace-keyed adaptor still looks the
> room up in `home_rooms` — the operator's choice, per `policy.toml` — and a
> scope-keyed one resolves its own conversation, the room whose batch the
> runtime already handed it. `culture_grant` is the first user.

### 7.5 The corollary: an objective that needs one store is not a Dreamer adaptor

The Dreamer's cost over a scheduled completion is the loop — the guard, the
ledger, the budgets, the turn cap, the whole of §2's table. An objective that
consults nothing but the batch it was handed pays that cost for nothing, and
worse, *inherits the vocabulary of research* for an answer that did none.

§5 already contained the honest version of this and it was read as an
exception: `RoomPortraitSynthesizer` was *"the one legitimate case"* because
its objective is executed in code and a third model call would reintroduce the
risk the tier removed. **Making everything an agent is not the goal** — that
sentence survives every deletion since.

So the test is not "does it call tools". It is one question, answerable per
adaptor and not answered here:

> **Name the second store this objective would consult if it could. If there
> isn't one, it is a scheduled completion and should be built and measured as
> one. If there is one, it is under-granted, and the grant — not the prompt —
> is the fix.**

Applied to the four above, on the evidence in §7.3: proactive has its answer
and is one verb short of the worked example (`where_been`); culture was asked
for a join and handed one table, and its own bundle named two stores it should
read (`standing`, `notes`) — **both granted and rendered on 2026-09-17, with
the objective unchanged**, which is the half of the fix the grant cannot do;
the two note objectives have not been asked the question, and should be, before
either is rewritten again.

**And the question has a prerequisite this section did not state.** An
objective cannot answer «name the second store» while the doctrine above it
already answers «answer from the text you were handed» — an objective's silence
about what to find out is not read as silence, it is filled in from the
preamble. That is why the culture grant could land with the objective untouched
and nothing look wrong. See [13](13-doctrine.md) §8 (D10): the doctrine is the
runtime layer, the job is the objective's, and until the two are separated the
test above cannot be answered in the file that owns the answer.

This is also why the failure is invisible rather than loud: §7.1's tautology
is a PASS at every gate the runtime owns. Naming it is the only instrument
this bundle has for it today — see [17-failure-classes.md](17-failure-classes.md) §11.

## Status / Checkpoint

2026-08-24 — The mechanism behind F1, unchanged. Cited by [28](28-language-tier.md) §2 — all three language adaptors are tools-less, so each is a single completion.

2026-09-17 — §5's five participants are gone and §7 replaces them with the four that run. The node's original finding (no tools, so no loop) is now the smaller half: three of four adaptors are still single completions, but the tool count was never the measure. §7 states the one that is — **an answer that could have been written from its own prompt has wasted the loop** — and names the grant, not the prompt, as the fix. §7.3 checks all four objective texts; the proactive objective already drives a real research loop and is the precedent the rest should be argued from.

2026-09-17 (later) — §7 re-read against the tree after the culture grant landed. Two of four adaptors hold tools now, not one: `CultureAdaptor` went `frozenset()` → nine verbs with `standing` and the note index added to its inputs, the scope-keyed `home_room` resolution §7.4 called unwired is wired, and `where_been` is in one grant rather than none. Every correction is marked in place rather than rewritten away. **The grant landed and the objective did not move**, and §7.5's test cannot be answered while the doctrine above an objective already answers it — see [13](13-doctrine.md) §8 (D10).
