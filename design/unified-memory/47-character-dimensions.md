---
type: design
title: "Character dimensions and claim slots"
description: "Define recurring trait dimensions, structured hypothesis explanations and reading claims, with citation-computed support and bounded rendering."
timestamp: '2026-10-22'
edges:
  - {dst: 45-persona-adaptor, rel: extends, provenance: asserted}
  - {dst: 18-message-readings, rel: extends, provenance: asserted}
  - {dst: 15-memory-map, rel: depends_on, provenance: asserted}
  - {dst: 25-memory-graph, rel: depends_on, provenance: asserted}
---

# Character dimensions and claim slots

Status: **proposed, docs only**, 2026-10-22. No schema, validator, renderer,
objective or live state is changed. This is the content contract for phases
2 and 4 of [18](18-message-readings.md), not an implementation receipt.

**Operator, 2026-10-22:** describe the whole Character from every eligible
medium, using generic dimensions and abstracted patterns. Follow
[ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md):
Character is the tier; person, room, unit and Nora are kinds; an entry has
profile, traits and hypotheses parts. Existing implementation names below
are references to the deferred migration, not new vocabulary.

## 0. What stays

[45 §0](45-persona-adaptor.md#0-this-reverses-prose-no-schema-deliberately)
keeps the lessons of DEC-184/DEC-290: **slots are named; their contents are
prose**. Code checks required slots and non-empty values, never enumerates
what a room can invent. No part or dimension is compulsory: one line is a
valid entry. Citations, mode, state and typed links remain columns, never
markup inside content. DEC-302 still holds: synthesis, caps, and re-judgement
of every held line each session. A 360-degree model means every dimension
can be expressed when grounded, not that every entry must fill every box.

This holds unless a frozen coverage sample demonstrates a recurring pattern
that fits none of the dimensions without losing meaning; that reading would
reopen the closed set rather than adding an arbitrary attribute bag.

### Existing formats and the collective gap

The room/unit prose path has the same gap as person traits: the current
[culture objective](../../nora/dreamer/objectives/culture.md) requires the body
to be prose with “No headings”. [12 §4](../cognition-loop/12-schemas.md#4-character)
proposed sections on 2026-09-28 for person, room, unit and Nora, but those
Character headings were not built; §4a's proposed Lessons headings are not
an implementation receipt either. A design table alone does not give readers
structured input.

Built precedents are the note grammar in
[`note_body.py`](../../nora/domain/note_body.py), Standing's section columns
and non-blank checks in
[`0076_standing_sections`](../../nora/infra/sql/migrations/versions/0076_standing_sections.py),
and gatherings' typed fields and per-field modes in
[`gatherings.py`](../../nora/domain/gatherings.py). Reuse their distinction
between checked structure and open content, without claiming that the proposed
Character/ Lessons heading grammars already run. This node supersedes 12 §4
for Character only; Lessons remains outside this change.

## 1. The dimensions

**Recommendation, 2026-10-22:** retain nine trait dimensions. They answer
separate questions; rhythm and place are context of a habit, not additional
kinds. Profile and hypotheses remain parts, not competing trait dimensions.

In the table, P = person, R = room, U = unit, N = Nora. Collective claims
are about observed collective practice, never a majority vote over private
member traits. A room or unit has no skill or inner feeling of its own;
member capabilities stay on the members. Collective disposition describes
observable response, not a mind. All examples are invented.

| part / kind | applies to | question, boundary and reason to keep it | example | counter-example and its home |
|---|---|---|---|---|
| profile (existing kinds) | P/R/U/N | Who or what is it? Keep [75](75-typed-identity.md)'s typed facts unchanged: actors have name, role, relationship, attribute, instruction; collectives have name, purpose, norm. Stated facts/rules are distinct from recurring observed practice. | A room's stated purpose is discussing books. | Repeatedly discussing novels is a `topic`, not a stated purpose. |
| `topic` | P/R/U/N | What do they engage with? Keep interest, depth and stance together; interest does not imply liking or expertise. | Follows football tactics and compares formations in depth. | Disliking match-day crowds is `preference`. |
| `habit` | P/R/U/N | What recurs, in what rhythm and abstract setting? Merge rhythm/place here: neither independently describes an action. | Usually reviews the week's reading on weekend evenings in the group. | One planned library visit is a reading/Register event. |
| `skill` | P/N | What can the actor demonstrably do? Keep capability separate from interest and occupation; a collective's roster of skilled people is not its own ability. | Can debug a small SQL query independently in observed exercises. | Enjoying database discussions is `topic`; a job title is profile. |
| `preference` | P/R/U/N | What is favoured or avoided in a context? Keep polarity; a room's observed choice is scoped to the room, never asserted of each member. | Prefers short written summaries for routine updates. | Consistently writing brief replies is `style` without evidence of preference. |
| `disposition` | P/R/U/N | How do they tend to respond under a trigger? Keep behavioural tendency separate from delivery form; feelings require evidence of feeling, and are actor-only. | The room tends to slow down and ask questions when someone is confused. | Using gentle wording is `style`; one upset reply is a reading. |
| `style` | P/R/U/N | How do they communicate? Keep language, register, length, timing and medium together as communication form. | Usually answers routine questions in brief, informal text. | Checking messages after dinner is `habit` unless it describes reply timing. |
| `value` | P/R/U/N | What priority is shown in choices, especially trade-offs? Keep observed commitment separate from liking and stated norms. | Repeatedly chooses accessible venues despite extra travel. | A stated accessibility rule is profile `norm`; one choice is a reading. |
| `social` | P/R/U/N | How do they relate, with whom and in what role? Keep recurring interaction separate from a named relationship fact. | Often mediates between newcomers and regulars during disagreements. | A stated sibling relationship is profile `relationship`. |
| `goal` | P/R/U/N | What continuing aim organises action over a horizon? Keep sustained pursuit distinct from recurring means and one-off plans. | Works towards completing a language course over the coming year. | Booking a lesson next Tuesday is a reading/Register event; weekly practice is `habit`. |
| hypotheses / `explanation` | P/R/U/N | Why might current induced traits occur? Keep a proposed cause falsifiable, separate from the pattern it explains. | Perhaps brief replies reflect limited evening time; predicts longer replies during a quieter window. | Brief replies observed repeatedly are `style`, not an explanation. |

Every starting trait dimension is retained because its question cannot be
answered by a neighbour without changing the claim. `rhythm` and `place`
are rejected as kinds because action + rhythm + abstract setting is one
habit; communication timing stays in style when it qualifies communication.
For overlap choose the question the line answers, not two copies of one
pattern. A social role invented by the group still fits prose; neither the
role nor a skill level nor a horizon becomes a closed value vocabulary.

A told hobby, preference or capability remains a source-backed profile
attribute under 75 where applicable; it does not bypass the recurring-evidence
rule to become an observed trait. A stated collective norm stays profile
whether or not the observed practice agrees with it.

### Mapping the proposed Character sections

Every 12 §4 section has a home below. Room mappings apply to unit too; unit
claims preserve differences across rooms instead of attributing one room's
practice to the whole unit. None of the old headings is a required dimension.

| 12 §4 section | kinds | home in this model, or reason dropped |
|---|---|---|
| `How they talk` | person, room, unit | `style`; affection/insult patterns may instead answer `social` or triggered `disposition`. |
| `What they care about` | person | `topic` for engagement, `value` for demonstrated priorities, `preference` for liking; classify the actual claim once. |
| `How they decide` | person | `disposition` for triggered response, `value` for a repeated trade-off, `habit` for a recurring decision practice. |
| `What they reliably do` | person | `habit`; demonstrated capability belongs to `skill`, continuing pursuit to `goal`. |
| `Who they are close to` | person | `social` for interaction; a stated relationship remains profile. |
| `How things get decided` | room, unit | `habit` for the collective process, `social` for recurring roles, `value` for demonstrated priorities; stated decision rules stay profile `norm`. |
| `Names and where they came from` | room, unit | Names are profile `name` as already amended by 45 §2; an origin story stays a source reading or Register context, not a trait. A recurring use of the name can support `style`/`social`. |
| `What they do together` | room, unit | `habit`, qualified by participants and setting; recurring engagement may answer `topic`. No group `skill` is inferred from a member's action. |
| `Across rooms` | unit | A trait's `context` names the room differences. It is a scope/comparison axis, not a tenth dimension; never average away conflicting practices. |
| `How I come across` | Nora | Observed reception supports `social`, `disposition` or `style`, with response-episode evidence; no self-attestation from her earlier account. |
| `What they come to me for` | Nora | `social` for her recurring role, with `topic` as relevant; requests alone do not prove `skill`. |
| `How I sound here` | Nora | `style`, scoped to this room/context. |
| `Unsure` | all four | Drop the catch-all prose section: a causal proposal uses hypotheses with prediction/window; incomplete observations remain readings, and disputes/unknown evidence retain their existing typed states. No unsupported trait slot is required. |

Content about what Nora should do belongs to Lessons; her current position
belongs to Standing. Neither is made a Character dimension merely because a
legacy account contained it. The other dimensions in §1 remain available to
collectives where applicable even if 12 §4 lacked a heading for them.

### Where the other aspects of a life belong

**Coverage mapping, 2026-10-22:** the nine dimensions are not
nine exhaustive profile fields. Knowledge belongs to `skill` when demonstrated
as an ability to explain or apply it, or `topic` when the evidence shows
engagement only. Memberships and affiliations belong to profile
`relationship`/`attribute` when stated, or `social` for an observed recurring
interaction pattern; participation alone does not establish membership.
Life stage, occupation and possessions concern profile, not additional trait
kinds. Today's contract distinguishes dedicated keys from fallback attributes:
[75](75-typed-identity.md) gives occupation a dedicated typed key, and
[45 §3](45-persona-adaptor.md#3-the-reasoning-modes-enforced) permits dedicated
profile facts such as occupation to be `deduced` from at least two eligible
premises, subject to its other checks. The `told`-only rule in 75 applies to
fallback `attribute` values; it is not a universal ban on deduced profile facts.
A stricter no-inference rule for occupation, life stage and possessions is
proposed in §7, not an operator amendment to today's contract. The bus-driver
example below remains a hypothesis: a route match is not evidence of occupation.

**Proposed sensitive-category rule, awaiting the operator:** health, religion,
sexuality, politics and finances are never inferred, never stored as traits or
hypotheses, and stored in profile only when the subject themself explicitly
told it, with the original speech act, time and scope preserved. This restriction
would take precedence over all nine dimensions and the affiliation mapping;
third-party statements, observed interests and outside evidence cannot bypass
it. It is a recommendation, not a claim about today's validator or permission
to widen disclosure. The operator position is recorded in §7.

## 2. Slot templates and storage

### Traits

Every trait has the following logical slots. Slots may carry explicit unknown
context; unknown is not an empty required string or an invented circumstance.

| slot | owner and representation |
|---|---|
| subject | Existing entry key, resolved by code to its kind and readable display name. Never a model-written identifier in text. |
| dimension | The line's `kind`, one of §1's nine; no duplicate field inside content. |
| statement | Non-empty prose stating the recurring pattern, abstracted from the observations. |
| context | Non-empty prose saying when, in what abstract setting and with whom it holds, to the extent supported; explicitly mark unspecified aspects. No street address or coordinates. |
| strength | Code-computed distinct occasions, first/last evidence time and distinct HKT days, resolved from citations; never accepted from model output. Unknown/unreadable roots give unavailable support, not zero or guessed times. |
| exceptions | Optional non-empty prose for evidenced limits or counter-patterns. An exception is not silently discarded to preserve a tidy trait. |

Dimension-specific required prose slots:

| dimension | extra slots |
|---|---|
| topic | `interest`, `depth`, `stance` (explicitly unknown when not supported) |
| habit | `action`, `rhythm`, `setting` (an abstract place or explicitly unspecified) |
| skill | `capability`, `level` (what the evidence demonstrates, not a credential inferred from interest) |
| preference | `object`, `polarity` (favours, avoids, mixed or a qualified prose description) |
| disposition | `trigger`, `response` |
| style | `language`, `register`, `length`, `timing`, `medium` (unsupported aspects explicitly unspecified) |
| value | `priority`, `tradeoff` (observed choices, not assent alone) |
| social | `counterpart`, `role`, `pattern` (display-safe prose; typed subject links stay outside content) |
| goal | `aim`, `horizon`, `pursuit` (evidence of continuing effort) |

These slots qualify one statement; they do not add more claims. A disagreement
between slots and statement refuses the line at semantic review. Required-slot
validation alone cannot establish entailment or correct classification.

### The trait-versus-event rule

**Proposed stricter common floor, not today's contract:** at least **three
distinct supporting occasions across at least two HKT calendar days**, for
every trait, including `deduced` traits. Today 45 §3 permits an `induced`
trait from **one Rollup aggregate key**, and a `deduced` line from **at least
two premises** without this recurrence floor. The proposal removes both
exceptions to the common occasion/day requirement; it retains the separate
premise-validity checks. An occasion is a distinct underlying act/interaction or bounded episode,
not the fire-turn “occasion” (code `Event`), and not a message count: replies, attachments and duplicate reports of the same
occasion count once. A multi-day single trip remains one occasion. Distinct
media or authors alone never establish recurrence.

Code resolves and deduplicates Record roots through readings, attachments,
episodes and aggregates, retaining an occasion's actual event time; when it
cannot establish event time it uses the assertion time explicitly marked as
such. A source with unresolved dating cannot supply a counted day. For a
multi-day occasion, the day of its start counts once. Re-reading, re-indexing
or rebuilding identical roots changes neither counts nor evidence age.

A Rollup key is **not an exemption**: it must expose enough roots and interval
metadata to prove the same occasion/day floor. Otherwise support is unavailable
and the write is refused. This amends 45 §3's single-aggregate shortcut and
extends its `deduced` premise checks with the trait floor; those checks still
apply. A single occasion stays a reading or an affair in Register, even if it
is dramatic, repeated in chat or asserted to be typical. Three qualifying
occasions are necessary, not sufficient: the writer and independent measure
still check whether they support the claimed pattern.

### Hypotheses

The subject remains the entry key; `kind=explanation`. Required content is
`proposed_cause` (X because Y) and `alternative` (a plausible different cause
considered, not an empty disclaimer). Existing typed `draws_on` links name the
current induced traits explained. Existing prediction, observable and window
columns remain required, with no duplicates in content. Citations ground the
proposal; links to traits are never citations. Alternatives need not be true,
but must remain explicitly hypothetical and must not smuggle in asserted facts.

The independent verdict judge, its closed-window trigger and its distinct
confirmed/refuted/expired/unknown outcomes remain [45 §3](45-persona-adaptor.md#3-the-reasoning-modes-enforced)'s.
Assent is not confirmation; confirmed explanations remain hypotheses.

### Reading claims

Each claim in a batch reading carries `about` (resolved subject links, with
unresolved references explicit), `what` (prose preserving the source's claim),
`happened_at` (a date/interval or explicit unknown), `where` (optional abstract
place, only when stated), `how` (the existing speech act), and the existing
source message ids. Authors and assertion timestamps are obtained from those
messages, not confused with the event's time or the reading's build time.

Resolve relative dates against the source message's timestamp in HKT. An
invented message dated 2030-04-01 saying a visit is planned for tomorrow
produces 2030-04-02 with `how=planned`; it never establishes that the visit
occurred. Ambiguous relative expressions remain unresolved with their source
wording and reference time, never guessed. No place is inferred from a photo,
GPS or a usual habit into a chat reading's `where`. A reference to an address
is abstracted only as far as its meaning supports; otherwise omit the place.

### Storage recommendation and phase-4 changes

Use **one JSONB content slot object per line**, alongside existing typed columns,
and an analogous per-claim object inside the versioned reading payload.
Keep subject/dimension, citations, mode, state, `draws_on`, prediction fields
and computed support outside the content object. Do not store a second full
model-written `text` that can contradict slots: any retained text column is a
code-rendered projection. Profile's `fact_key`/`fact_value` and effective dates
stay [75](75-typed-identity.md)'s contract, not this prose object.

JSONB permits named prose slots without a column for every descriptive phrase.
Per-slot columns would multiply sparse fields across nine kinds; labelled prose
would require re-parsing and make missing slots ambiguous. Neither is needed.
Code validates object shape, required non-empty strings, unknown markers and
applicable kind; prose values remain open, including group-invented roles.

Phase 4 must change the part/kind CHECK with the ADR 0026 rename, replacing
`trait` with the nine trait kinds; extend the existing shared validator in
`nora/domain/persona.py` with shapes, kind applicability, citation-derived
strength and the recurrence floor; and update the shared renderer and its
prompt, portal and index consumers to render the canonical slots. Add the
content object and required support metadata in that same migration, preserving
append-only history. Legacy free prose is not mechanically classified as a
verified trait: re-evidence it through the existing writer, leaving unsupported
lines out of the accepted card. No extra writer, value ontology or model call
per dimension. The phase-4 objective migration must replace the culture objective's
**“No headings” / prose-only `body` rule** with the common structured-line
contract for room and unit as well as person and Nora, and reconcile its
no-expiry prose instruction with §4's measured decay. The old prose writer
retires at the switch; do not leave its output feeding the typed collective
writer. This doc does not edit that objective. Reading payloads and validation
land separately in phase 2.

## 3. Evidence from every medium, abstracted

Use the owning stores in [15](15-memory-map.md) and [25](25-memory-graph.md),
through [45 §4a](45-persona-adaptor.md#4a-proposed-person-input-extension--2026-10-20)'s
single evidence assembly. Availability in this table is a possible basis,
not authorization or proof that every input path is built.

| evidence medium and real store | dimensions it may support | abstraction and limits |
|---|---|---|
| Chat: `messages`, edits/reactions, and proposed Record message readings | all nine, when the content demonstrates the property | Atomic speech acts become patterns only over qualifying occasions; a self-description alone is not observed skill/value. Never copy messages, ids or one-off plans into traits. |
| Media: `media_objects` readings (`title`, `description`, `content`) through message attachments; `social_item` and `social_perception` through readable social items | topic, habit, skill, preference, disposition, style, value, social, goal, only where actually evidenced | Depiction/sharing is not ownership, residence, expertise or liking. Resolve author and attachment/item roots; copies of the same bytes or occasion add no support. Social location remains separately gated. |
| Presence: Record fixes/polls through Rollup `presence_stays`, trips, outings and co-locations | habit and social; goal only with separate evidence of aim | Episodes may yield an abstract place category and time-of-week rhythm, never coordinates, addresses or inferred residence. **Blocked for the shared Character card** by 45 §4a's location ceiling; abstraction does not grant disclosure permission. Raw fixes are never writer input. |
| Notes/memos/events: Notebook Register head revisions | context for any dimension, not independent support | An open plan, cancellation or settled affair qualifies context. Resolve its citations to eligible Record/Rollup roots; do not count the summary as another witness or promote intention into achievement. |
| Acts/outcomes: recorded sends/tool results/artifacts, `rollup_response`, and the proposed social-act episode in [50](50-learning-from-acts.md) | style, social, disposition, preference; skill, value, habit, goal, topic when demonstrated | Abstract repeated action/reception in its context. Silence is not dislike; unreadable outcome is unknown. Learning about Nora never assigns her action to a member, and positive reaction never proves correctness. |
| Rollup counts: place, rhythm, tie, play-profile aggregate keys | habit, social, topic/style where the measured property supports them | Describe the measured pattern over its actual interval; counts do not establish liking, motive, skill or value. Location-bearing aggregates stay blocked. Require root-based support; a new build is not new evidence. |

The existing media above keep their owning stores. **Amended recommendation,
2026-10-22:** external hypothesis checks need one new append-only Record table,
`verdict_evidence`, owned by the independent verdict adaptor, as specified below.
This explicitly replaces this node's earlier “no new evidence store” claim for
that external path only. The memory index locates items and typed links; it is
not a witness. Every input keeps source eligibility, unit scope,
subject membership and disclosure checks. Notebook entries and Nora's earlier
conclusions never manufacture independent corroboration.

### The composition ladder — observations to checked explanations

**Operator addition, 2026-10-22:** cognition composes upward over the same
stores; it does not stop at counts. The Character writer still owns its
entry and an independent judge still owns hypothesis verdicts. No new tier,
medium-specific writer or unbounded external-search loop is introduced.

| rung | what is stored and by whom | abstraction and provenance |
|---|---|---|
| Record observation | Existing messages/readings, attachment/media readings, social items and presence fixes/polls keep their owners. External lookup attempts/results would be new Record rows in proposed `verdict_evidence`, owned by the independent verdict adaptor. | Source-specific detail stays in its scoped store with authorship and source/assertion time. Reading claims preserve speech acts; raw observations never become public card text. |
| Rollup pattern | Existing deterministic episode/aggregate builders store intervals, counts, computed patterns and roots/build identity. | Code folds repeated evidence; it does not name motives or occupations. Not every medium needs a new aggregate: chat/Instagram/media can use existing eligible episodes/counts or feed deduplicated Record occasions directly to the next rung. |
| Trait | The existing Character writer stores one `habit`, `topic`, `style` or other applicable dimension with §2's slots and code-computed strength. | A recurring pattern, not the raw route, post, message or photograph. Context and exceptions constrain it. Recurrence and all disclosure checks apply before admission. |
| Abduced hypothesis | The same writer stores an explanation linked by `draws_on`, proposed cause, alternative and an observable prediction/window. | A possible cause, not a new profile fact. It says what would distinguish it from the alternative and which outside observation, if needed, would test it. |
| Independent check (proposed external path) | A phase-4 evidence-collection runner owned by the independent verdict adaptor, never the Character writer, performs an authorized lookup and hands retained evidence to the judge. Its query, source URL, retrieved content, retrieval time, outcome and available publication/effective time would be retained in new append-only `verdict_evidence` rows, linked to the trace. The table, append path, scoped reference resolver, retention and judge-input extension do not exist today. | The result is evidence about its actual proposition. Missing dates stay unknown; a search snippet is a locator until the source is read. Private roots remain private; public evidence does not inherit permission to reveal them. |
| Verdict | Today's write records confirmed/refuted/expired/unknown and judge identity/time. Phase 4 must add a proposed `verdict_evidence_refs` column on the superseding verdict row and extend the verdict write to persist references to the retained evidence. No evidence-reference write exists today. | Confirm only the specified prediction, not all causal assertions or a person's identity. No automatic profile promotion; retained evidence lets another reader reconstruct the judgement. |

Presence already has the lower rungs: the presence Rollup in
[`nora/analytics/rollup.py`](../../nora/analytics/rollup.py) folds fixes into
stays, trips, places, occupancy, rhythm and corridors, reached through
`routine_read` / `where_been` (DEC-216). **The next rungs below are conditional
on resolving 45 §4a's location privacy ceiling and R-31, not authorized by this
design.** An invented repeated weekday corridor lasting hours can support
an abstract habit such as “regularly travels a fixed route on weekdays”.
“Drives a fixed route on weekday shifts” additionally needs evidence of
driving and shifts; coordinates alone show neither. An abduction could be
“perhaps a bus driver”, with commuting/passenger travel as an alternative
and a prediction that the abstracted corridor matches a public bus service.

An authorized lookup could check a public bus-line source. A matching line
confirms that prediction only: it **does not prove the person drives a bus**.
A contrary result can refute that prediction; inaccessible or insufficiently
specific evidence yields unknown, never confirmation by plausibility. A job
claim needs its own direct grounding before it could enter profile. Instagram
publication rhythms, chat interaction patterns and repeated media-demonstrated
activities climb the same ladder with their own roots and typed dimensions;
no compulsory presence-shaped intermediate store is imposed on them.

**Current implementation limit (2026-10-22):**
[`nora/language/persona_verdict.py`](../../nora/language/persona_verdict.py)
sets `tools = frozenset()`: the judge has no read tools and is handed the
window's rows, nothing else. In
[`nora/infra/sql/persona.py`](../../nora/infra/sql/persona.py), `record_verdict`
accepts key/line identity, verdict, verdict_by and now_ms; it has no
evidence-reference argument or corresponding persistence path. Retaining a
web result somewhere does not make it available to that judge or cited by its
verdict. **Both external evidence acquisition/delivery and durable verdict
references are phase-4 work, not existing capabilities.**

**There is no existing durable lookup-evidence path to reuse.**
[`nora/domain/tool_call_log.py`](../../nora/domain/tool_call_log.py) records only
successful calls to cacheable tools, scoped to one conversation and read back
only by `recall_lookups` in that same conversation. Its port exposes recent
entries, not lookup by evidence reference. The writer in
[`nora/agent/guards.py`](../../nora/agent/guards.py) skips non-ok outcomes.
That cache cannot supply failed attempts or the scoped references needed here;
trace availability alone is not durable Record evidence. Its conversation
boundary must not be widened to serve this design.

**Proposed phase-4 storage choice:** add one append-only **Record** table,
`verdict_evidence`, owned by the independent verdict adaptor. Prefer it over
extending the conversation recall cache: the owners, read boundary, outcomes
and retention differ. This is a new evidence store, explicitly amending the
earlier “no second evidence store” statement, not a claim that existing plumbing
already satisfies the contract. It is a narrow table for hypothesis checks,
not a second general memory system or model-written knowledge tier.

The table carries an immutable row reference and attempt reference, the scoped
hypothesis/entry and unit, judge-run identity, eligible audience/source scope,
permitted query, source URL, content when retrieved, attempt/retrieval time,
source publication/effective time when known, outcome/error and trace reference.
Absent content or dates stay absent, never a successful empty result. Scope is
at least as restrictive as the hypothesis and every input authorizing the lookup;
a public URL does not make the private reason for checking it public.

**Proposed phase-4 append and read path:** an evidence-collection runner in the
independent verdict adaptor executes a bounded lookup only after the outbound
payload policy is authorized. The Character writer neither runs the lookup nor
selects the verdict; it supplies only the hypothesis and predeclared observable.
The runner appends an attempt row before executing the permitted lookup, then
an outcome row linked to that attempt for success, failure, timeout or
unavailability, without rewriting either row. A started attempt without an
outcome is incomplete, not successful evidence. A failed append cannot be
replaced by a trace-only receipt or allow an uncited verdict to commit.

Phase 4 must add a **scoped reference lookup** over `verdict_evidence`: both
judge-input assembly and `verdict_evidence_refs` validation resolve immutable
row references through the same reader. It checks the requested unit,
hypothesis/entry, audience/source eligibility and row availability before
returning content; it never fetches another conversation's recall cache.
The model judge may remain tool-less: the runner resolves the retained rows
into its input and records exactly which references it received. A failed or
unreadable reference yields unavailable evidence, never guessed content.

Add a proposed `verdict_evidence_refs` column to the **new superseding verdict
row**, and extend `record_verdict` and its caller to validate and persist it in
the same verdict transaction. References identify `verdict_evidence` rows,
whose trace links are supplementary provenance, not a substitute for retained
content. Validate that each reference resolves under scope and was delivered
to this judge. Confirmation requires durable supporting outcome rows;
failed/unavailable attempts are retained and referenced as the reason for an
unknown verdict, never as positive support.

**Proposed retention:** keep attempts and outcomes, including failed/unavailable
ones, for at least the lifetime of the referencing hypothesis/verdict revision
history; outstanding attempts remain until that hypothesis history retires
under its retention policy. Do not inherit the recall cache's window or trace
expiry. Phase 4 must implement this retention alongside append and scoped lookup.
If an explicit privacy purge removes evidence, resolution must report it
unavailable and historical verification remains unmeasured; neither a URL nor a
trace may reconstruct missing private content. Retention grants no wider read
permission. None of this table, runner, resolver, retention or verdict-write
plumbing is built by this docs change.

**Proposed extension to 45 §3 (not built): external evidence is a permitted
hypothesis-verdict source**, beside a Rollup count or described event. Retain
the independent judge and closed-window timing. The observable names the public
property, its time relevance and what would confirm or refute it before the
lookup. Read through permitted web tooling and retain the source in the proposed
`verdict_evidence` Record table with its trace link, not in the recall cache. Outages, unreadable
sources, ambiguous matches and undated evidence that cannot establish the
window give unknown. Expired means the observable was checkably absent in its
window, not that a search failed. The external result cannot corroborate the
private observation by merely repeating text supplied in the query.

**Proposed outbound rule — operator decision required:** an outside lookup
may send only a minimal, unlinked public description, such as abstracted public
street or stop names and a public service question. Never send coordinates to
another tool (R-28/DEC-227), a person's name/identity/handle, private address,
raw route trace, individual timestamps or a query tying the public pattern to
someone. Street/stop labels are still location information: this proposal
neither permits reverse-geocoding coordinates via another tool nor overrides
R-31 or the durable-claim block. If an allowed abstract query cannot test the
prediction, leave it unknown. No lookup is run here.

## 4. Caps, rendering and decay

**Starting values, to size on the frozen sample:** replace five total traits
with **nine total current traits per entry**, pooled across dimensions. No
per-dimension quota or minimum; several lines may cover one dimension while
others stay absent. Keep three hypotheses whose windows are open.
Profile storage stays uncapped as in 75.

The rendered card has a **1,200-character total budget**, including labels,
context, support summaries, profile and hypothesis text. Each trait/hypothesis
has a **300-character canonical rendered-content ceiling** across its prose
slots, not 300 per slot; code metadata is separately counted in the total
card budget. Optional unknown aspects render compactly. Omit whole lower-ranked
lines when full; never truncate a qualification, an exception or hypothesis
label into a misleading claim. Use the existing precedence, then newest actual
support, then stable line order to select; unread/omitted dimensions remain
retrievable through Views, not fabricated into the card. Authored doctrine and
roster blocks are outside this Character-card budget. Storage caps and render
omissions are distinct, reported separately.

**Decay starting values:** keep 60 unsupported days for topic, habit,
preference, disposition, style and social; use 180 for skill and value, whose
evidence may arrive less often. Goals use the earlier of 60 unsupported days
or an evidenced horizon end, unless new evidence establishes continuation.
An unknown horizon uses 60 days, not immortality. Evidence of completion or
abandonment is re-judged at the next session even before decay. Profile has
no automatic decay; hypotheses retain their existing prediction-window rules.

Decay uses last actual supporting evidence, never write/rebuild time. Re-judge
every held line every session; after its horizon unsupported lines retire.
The longer skill/value interval means retained evidence is older, not that a
capability is guaranteed current. Reopen these numbers on measured support
intervals, card omissions and answer quality; no production sizing is claimed.

## 5. How it is measured — CMS

**Comprehend:** freeze eligible inputs, source revisions, scopes, timestamps,
root/occasion mappings and the composed model input. Include counter-evidence.
Interpret each source once into the existing typed assembly. Never infer a
person's identity, residence or ownership from indirect clues.

**Measure:** the build must refuse a write atomically for missing/empty slots,
inapplicable kinds, unreadable support or a failed occasion/day floor. Compute
strength from citations in code, including aggregates; the model cannot submit
counts. Extend the existing R-18/R-28 screen to every prose slot and the complete
canonical rendering: no identifiers, coordinates, street addresses, quoted chats
or raw event log in a trait. A habit's abstract setting is still subject to
R-31; merely removing coordinates is insufficient. Rejected output never
advances source progress (45 §4a).

Syntactic screens cannot decide whether prose is abstract enough or entailed.
Use synthetic negative/positive checks for raw identifiers/coordinates and
single-occasion repeats across media, plus an independent source-grounded
judgement of abstraction, correct dimension and pattern-versus-event on a
frozen sample. Unsupported claims fail; unreadable evidence is ERROR, never
PASS or an event count of zero.

The coverage measure freezes a sample of current shadow chain-head trait lines
across Character kinds, stratified by source medium where available, before
classification. Report counts per proposed dimension, events masquerading as
traits, unsupported/ambiguous lines, and empty dimensions separately. The event
share is event lines / all sampled current trait lines, with unreadable lines
reported separately and never silently removed from that denominator. Report
entries sampled and applicable dimensions represented; absence is not a failed
entry. A classifier other than the writer checks original eligible sources,
not just polished trait text. Compare the same frozen roots before/after the
build; freeze its acceptance thresholds before running it.

**Serialize:** bind each classification and deterministic refusal to the
sample revision, code/model identity, trace and reason, retaining failed and
unavailable results. Judge Coherence (one claim across slots), Consistency
(existing stores/writer), Conformance (scope and disclosure), Correctness
(source entailment and recurrence), and Completeness (all applicable dimensions
and evidence media considered). Keep raw evidence and classifications on
norty; only sanitized aggregate counts leave it. This doc neither reads
production nor measures the event share. Documentation tests prove links and
structure, not semantic acceptance. The Q1 switch gate remains unchanged.

## 6. Where it lands in the phases

[18's phases](18-message-readings.md#phases-in-order) remain in order:

- **Phase 2:** structured reading claims, event/assertion time distinction,
  relative-date handling and source-message visibility; new-message readings
  still have no consumer at this phase.
- **Phase 4:** trait kinds, content slots, support computation, hypothesis
  explanation slots, caps/renderer and re-evidencing, together with the one
  ADR 0026 Character rename migration and writer rework. External checks require
  the new independent verdict-adaptor lookup runner; the new append-only Record
  table `verdict_evidence` and append path including failed/unavailable attempts;
  its scoped reference lookup shared by judge-input assembly and verdict-reference
  validation; and retention independent of recall-cache/trace expiry. Add a new
  `verdict_evidence_refs` column on the superseding verdict row and the
  `record_verdict`/caller changes that persist validated references atomically.
  None of this evidence storage, delivery, resolver, retention or reference
  persistence exists today; lookup execution remains conditional on the
  operator's payload rule.

No objective, tool or schema changes here. No Q1–Q5 wording, answerability bar,
reply replay or operator-owned switch decision is changed.

## 7. Open positions

- **Presence-derived durable patterns:** recommend retaining the blocked
  default. An abstract setting still reveals whereabouts; only the operator's decision on a design proving
  the existing disclosure rule in every receiving room can reopen admission.
  The presence ladder is conditional on that decision, including durable
  trait/hypothesis storage; no implied permission comes from abstraction.
- **External lookup payload:** recommend the minimal public street/stop-name
  rule above, never coordinates or a person's identity. This awaits the
  operator's decision under R-28/DEC-227 and R-31; it is not a new egress
  exception and no outside lookup is authorized by this docs change.
- **Occupation, life stage and possessions:** propose requiring told evidence,
  never inference from habits, appearance, places or media, for profile claims
  about these aspects. This would narrow today's dedicated-key deduction path
  for occupation; fallback attributes are already told-only. It is an open
  operator decision, not an amendment or a claim about today's validator.
- **Sensitive categories:** recommend never inferring health, religion,
  sexuality, politics or finances, never making them traits or hypotheses,
  and storing them only as profile facts explicitly told by the subject
  themself, under existing source/scope rules. For 75's existing `gender`, `sex`
  and `birthday` keys, recommend the same subject-told-only/no-inference rule
  and no trait/hypothesis route; this would be an explicit constraint on those
  dedicated keys, not their deletion or an assertion that they are fallback
  attributes. Propose the same source restriction for `residence`, while treating
  it as location: a self-report alone does not settle R-31 or the unresolved
  location ceiling for durable Character claims. The operator must decide how
  the proposal applies to all four existing keys and residence's admission;
  neither that policy nor a migration is decided here. This is an open operator
  position, not an implemented restriction or newly granted disclosure permission.
- **Sizing and decay:** recommend §4's starting values, then size from frozen
  support intervals and rendered omissions. The repo supplies no measurement
  justifying a final budget or horizon for these nine kinds.
- **Semantic acceptance threshold:** recommend zero unsupported promotions in
  the frozen sample, with event misclassification and missing dimensions reported
  apart; freeze sample size and remaining thresholds before the phase-4 run.
  Those cannot be sized from this docs-only work. A green shape validator is
  never a replacement for that measurement or Q1.
