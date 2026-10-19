# Typed Identity and complete historical recovery

Status: implemented locally; independent review and live acceptance pending, 2026-10-19. This amends
35 and 45 on the operator's instruction: Identity is queryable factual data,
Character is recurring behavior, and one Persona session owns both. It does
not authorize the live render switch before coverage and reply validation.

## Evidence and scope

The first live shadow backfill wrote dated intentions as current identity
attributes. The Record retains their timestamps, but free-text attributes
have no named value and the renderer's write age describes the new backfill,
not the old statement. The source reader's newest-page policy advances past
older messages without reading them. Therefore a successful session does not
establish historical identity coverage. Production examples stay out of Git.

Keep the existing unit boundary, authorship and exclusion checks, precedence,
disputes, append-only revisions and single atomic persona_write transaction.
No second identity database or Dreamer, no new warehouse service.

## Identity contract

Extend existing persona_line identity rows with fact_key (text), fact_value
(JSONB), valid_from and valid_until (nullable epoch ms). Existing as_of is the
source assertion/evidence time, never synthesized from backfill wall-clock
time. A current typed fact revised without changing value, key, mode, teller,
effective bounds or premises retains its original as_of and support metadata;
its successor unions citations rather than discarding original grounding. This
also applies to text rewording. Explicit keep may add actual corroboration;
semantic entailment remains the writer's job, not an author-presence check.
Existing written_at remains transaction time; citations and told_by
remain the evidence and its speaker. Effective dates are supplied only when
the evidence establishes them; null means unknown, not today.

Initial keys: name, role, relationship, gender, sex, birthday, residence,
languages, occupation, dietary_restrictions, food_preferences, instruction,
purpose and norm. The key must agree with actor/collective and the existing
kind: common fields use their dedicated factual key and exact typed contract.
Do not infer gender, residence or dietary restrictions from names or silence.

Scalar labels use strings; languages, dietary_restrictions and food_preferences
use arrays of strings; birthday uses month/day and optional year, validated
as a calendar date (a recurring birthday may have no year). A relationship
uses a relation and a resolved person reference or an explicitly unresolved
name. Resolve references through the existing same-unit name boundary when
rendering, never display raw account ids. Authored names/accounts remain
owned by identity.toml and retain their existing precedence.

### Additional human attributes (2026-10-19)

A source-backed coverage audit found 32 missed rows across 26 account/field
groups, including height, self-reported age, hobbies, capabilities and ownership.
These are preliminary source-backed gaps, not a strict coverage or reply gate.
Some misses (languages, instructions and workplace qualifiers) already fit the
existing fields; the objective must preserve them beyond its examples. Private
sources, names and account identifiers remain outside Git.

Add one fallback fact_key="attribute", existing kind="attribute", for human
Identity only. Its value is exactly {name, value, unit?}: name is a nonempty
lowercase label with spaces/hyphens normalized to underscores; value is a
nonempty JSON string, finite number, boolean or nonempty list of nonempty
strings; unit is an optional nonempty source-supplied string. No null, nested
object, object list or extra key. A normalized name matching any dedicated
FACT_KINDS key is refused: birthday, gender, sex, residence and the other
existing fields retain their contracts. Collective Identity is unchanged.

Fallbacks require mode="told", with the existing teller, source, room and
provenance checks. They preserve explicit self/third-party statements, never
inferred personality. Source-reported age is age at its assertion date, not a
calculated current age or an inferred birthday/year. Units and conversions are
never guessed. A borrowed car or walking another person's dog establishes no
durable ownership. Plans, conditions and uncertainty must not be qualified only
in prose; the canonical key/value must independently carry the supported claim.

One fallback covers the measured gaps without ten more enumerated field keys,
a table, config knob, adapter or second writer. Reopen this choice only if a
measured consumer needs a dedicated typed contract for a specific attribute.
Existing line-length and disclosure/egress limits still apply to every label,
value and unit through the shared formatted surface. The canonical rendering is
"<name>: <value> <unit>" (unit omitted when absent), followed by existing assertion
and effective times. Lists render as comma-separated values and booleans as
true/false, never raw dictionaries or internal identifiers. The portal carries
the fallback JSON unchanged and displays that shared formatted text; retrieval
projection/hydration and remember retain the same structured value and time.
No migration changes the old schema or stored history.

One shared validator and formatter own these shapes. Prompt, portal and
memory projection use the canonical formatted value, so a conflicting model
text cannot override structured truth. The writer must therefore support the
canonical key/value independently of prose: text is not carried to other models
and cannot qualify a typed claim. If truth depends on a qualification the shape
cannot carry, leave the field unknown and retain the source in Record/event
history, without new JSON keys. A wedding plan and later name mentions do not
support even a generic partner claim; directly stated partner/spouse facts remain
eligible with their original assertion dates. Store history, not competing current
singleton values: explicit changes revise/supersede with evidence; retain
uncertain/conflicting claims through existing precedence/dispute semantics.

Migration adds nullable fields and preserves old rows; never mechanically
turn old prose into verified typed facts. Current legacy identity must be
re-evidenced and classified during backfill before accepted as cutover
coverage. Unknown facts are absent, not invented defaults. Legacy untyped
identity is not promoted into the typed profile or graph as settled truth.
Remove the 25-line storage cap on Identity; keep bounded rendering/retrieval.
Character and hypothesis limits remain unchanged.

## One session and lifecycle

The person and collective Persona sessions continue Identity -> Character ->
Hypotheses, then one persona_write. The independent verdict judge stays.
The graph-surface remember path must use this same typed write contract and
validation; do not introduce another truth writer or parse prose into facts
with regex. Update its existing tool schema coherently and measure routing
once the complete implementation is available. A temporary plan is Record or
Register evidence, not a stable identity attribute. No calendar date or new
event is fabricated when an occasion cannot be resolved.

Render-off preserves the legacy path while validating. Render-on disables the
legacy culture/character model writers. Before doing so, rewire every legacy
input dependency: Persona person context and the unit's room-account evidence
must read new Persona room accounts/revisions on the new surface, not wait
for a retired Culture writer. The switch remains reversible; no client or
legacy source tables are dropped in this change.

## Historical recovery

Separate the complete historical pass from normal latest-message updates.
Use existing cursor/runtime mechanisms where possible. A bounded resumable
pass tracks its source high-water mark and oldest-first progress per scoped
account. Read the union of eligible own messages and mentions in chronological
pages; advance only past items actually handed to a successfully committed
session. Never let max of two independently truncated streams skip unseen
messages. A failed/conflicted/unsettled run retains its page. Freeze the
source boundary so new traffic cannot make the pass endless. Resume after
restart; mark complete only after exhaustion. An explicit rerun must be
possible for the already-completed but incomplete first shadow backfill.

The same Persona writer folds each page into its account. Historical sources
retain their source time and do not imply present validity. Existing source
rows and pre-backfill identity history are retained. Do not copy uncited
Honcho conclusions into settled facts: use the card as a gap checklist and
trace recovered claims to original evidence. Keep missing/ambiguous sources
as unresolved coverage, not a successful migration.

## Acceptance and cutover

Deterministic regressions must cover typed round trips, invalid shapes,
unknown versus explicit empty restrictions, birthdays without an invented
year/date, moved residence with retained history, event intentions excluded
from stable attributes, conflicts/disputes, and source-time versus write-time.
Exercise real SQL, shared formatter, prompt/portal/index and remember paths.
Prove page completeness when own-message/mention streams interleave, failure,
conflict and restart occur, and when new messages arrive beyond the boundary.
Pin legacy-writer retirement and new unit/person input dependencies on the
render switch. Preserve all disclosure and retrieval rollback regressions.

Synthetic source-grounding acceptance vectors (invented sources; model behavior
must be measured separately, not inferred from corpus collection or the gate):

| Sources / held state | Required result |
|---|---|
| May 1: P1 explicitly says birthday is June 8; May 9: P1 offers an undated birthday greeting to someone else | Retain June 8 with May 1 assertion/support; no new corroboration from the greeting |
| Same held birthday; May 9: P1 explicitly corrects it to June 9 | Revise the value with the correction's source time |
| Regional weather requests, mall departure, and an unspecified home origin | Residence remains unknown; no settled-home inference |
| September 1: P1 plans an October 3 wedding with Jo; later mentions only Jo's name; clock is October 12 | Historical intention only; current fiancée/marital status unknown; no invented end date or automatic spouse |
| P1 uses an ambiguous colloquial partner label | Partner only; do not specialize to girlfriend, fiancée or wife |
| P1 explicitly states where they live and explicitly names their spouse | Positive control: preserve the supported residence and relationship specificity |
| May 1: P1 states height 178 cm and age 34 years; clock is October 12 | Preserve both as structured attributes with May 1 assertion/citation; no birthday or calculated current age |
| P1 drives a borrowed car and walks a friend's dog today | No durable ownership identity from incidental activity |

The existing typed `tool_selection` cases measure reply routing, not this
Persona objective. These vectors specify the writer acceptance still owed by
the bounded whole-change model run; no separate model harness is introduced.

After the full build: final gate and independent economical review, deploy
shadow, run bounded historical recovery, and produce per-account counts and
missing/ambiguous fact coverage without publishing private rows. Compare useful
Honcho card facts to grounded new values and inspect source messages for every
miss. Then run existing recall and reply gates with explicit budget/receipts.
No switch or parity claim on a model's self-report or an incomplete pass.
The existing Honcho post-switch hold/removal sequence still applies.
