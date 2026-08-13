---
kind: spec
title: "Nora Compute — the context channel: how research reaches Smith"
description: "make_artifact gains a context parameter carrying Nora's already-typed tool results (place/route facts) to Smith as a separate file, not flattened prose — place coordinates resolved Smith-side from the address Nora already relays, never widening what reaches Nora's own model context. Draws the place-vs-person trust boundary, corrects a wrong assumption about register_artifact's egress path, and adds hash-based provenance for the payload."
tags: [nora, spec, compute, smith, make_artifact, trust-boundary, provenance]
timestamp: '2026-08-13'
status: accepted
derives_from:
  - docs/superpowers/specs/2026-08-12-nora-compute-design.md
  - design/09-decision-index.md
---

# Nora Compute — the context channel

## 0. What this document is

`docs/superpowers/specs/2026-08-12-nora-compute-design.md` (§4) specified
`make_artifact(task: str, artifact_id: str | None = None)` — `task` as
Nora's own distilled brief, and nothing else. That shape shipped in Phase 4
and was tried against a real request the day after: a six-venue Cantonese
recommendation report for a group in Tsim Sha Tsui. Reading the actual
`task.txt` that reached Smith's container for that job is the origin of this
document — it is quoted in §1, because every claim below is answerable
directly from it rather than from a hypothetical.

**Revision note (2026-08-14):** the first draft of this document proposed
removing `place_info`'s coordinate suppression. An independent subagent
review found that proposal factually wrong — it isn't an untested accident,
it's DEC-60's own governing rule ("coordinates and geometry stay
server-side; the model handles names"), reaffirmed by DEC-63, DEC-64, and
DEC-69(2), and guarded by a dedicated test
(`tests/test_routes.py::test_place_info_returns_the_google_rating`). §3.1
below is rewritten to a design that doesn't touch that rule at all. The
review also found a real gap in §3.2 (a saved place name could leak through
`context` unfiltered) and several missing files in §7 — both fixed below.
Nothing in §2, §3.2's core conclusion, §3.3, or §4's mechanism needed to
change; only the coordinate-sourcing story in §3.1 and its downstream
consequences did.

This is not a Phase 5 implementation plan. It is the design the next plan
should be written from, the same relationship Phase 4's own plan had to
§4/§5 of the original spec.

## 1. The problem, grounded in a real payload

The live `task.txt` (`data/compute-workspaces/job_4e0b963a1bdf4a57bd73b5e0905fb0d9/task.txt`)
lists six venues. Five of the six carry a name, address, Google rating,
price band, a one-line reason, and a line reading
`Maps: https://nora.example.net/i/<slug>` (the sixth has no Google rating and no
link — Google simply didn't have one to give).

That link is the tell. `render_map`, Smith's own skill for embedding a map
(`nora/compute/smith/skills/maps/render.py:20`), takes
`locations: list[tuple[float, float]]` (plus an unrelated `title:` string) —
raw coordinates, nothing decodable from a shortened link. So the brief,
however complete it reads as prose, structurally could not have fed the one
skill built for exactly this kind of task.

The link is not carelessness, and — this matters for §3.1 — it is not an
oversight either. `place_info` (`nora/infra/routes/client.py:259-290`)
resolves a place's coordinate and explicitly never returns it: *"The place's
COORDINATE is resolved internally and never returned — same as `route`
(DEC-24)."* That is a deliberate, tested, multiply-reaffirmed design
decision (§3.1), not a gap. Nora never had the coordinate to hand over on
purpose. The Maps link was the closest thing she was holding that pointed at
a location at all.

**The deeper issue `task: str` alone exposes:** Nora's own tools already
return typed data — `place_info`'s `rating`/`price_level`/`open_now` are
"not free text" by that tool's own docstring; `route`'s duration/distance/
transit fields are the unwrapped typed half of DEC-46's field mask. Folding
all of that into one prose string forces a second, lossy translation step —
structured fact to sentence — for no reason other than `make_artifact`
having nowhere else to put it. This document adds that place, without
touching what Nora's own model context is allowed to hold.

## 2. Roles: comprehend is Nora's, build is Smith's — and stays that way

Restating the original spec's §4 line ("Smith receives only what Nora
chooses to hand it") more precisely, in this repo's own terms
(`CLAUDE.md`'s comprehend → measure → serialize spine, applied to the
Nora↔Smith seam specifically):

- **Nora comprehends.** She is the only component with a conversation, a
  roster, `identity.toml`, and every tool that resolves a name to a fact.
  Her output for a compute job is two things: `task` (prose — what to
  build, tone, structure) and `context` (§4 — the typed facts she already
  gathered, unmodified).
- **Smith measures and serializes.** Given task + context, it decides which
  skill fits, composes the artifact's own prose, executes the render calls,
  and registers the result with Drop. It does not gather anything of its
  own. No `route` tool, no `identity.toml`, no roster — not merely
  policy-withheld, structurally absent from the image.

This was raised as an open question ("should Smith do its own research,
given it has budget to spare") and closed here, for a reason independent of
budget economics: Smith cannot resolve a person's name to a location at
all — there is no path to `identity.toml` from inside its container, by
design (`agent.py`'s own docstring: "Deliberately NOT importing anything
from the top-level `nora` package"). Giving it that path is a materially
different, much larger change than anything this document proposes, and is
out of scope here.

**One narrow exception, added in this revision (§3.1): Smith may geocode a
public place's own address, using the Google Maps key it is already
issued.** That is not "research" in the §2 sense above — it resolves no
name, touches no identity, and the input (an address Nora already relays
freely) is data Smith already has. It is a rendering-input transform, the
same category as `render_map` turning coordinates into a base64 image.

## 3. The trust boundary: place data crosses freely, person data never does

Two categories of fact, deliberately not one:

### 3.1 Place facts — the address crosses; the coordinate is resolved Smith-side

A business's name, address, rating, and price band are already public — the
entire reason `place_info` can look them up on Google Maps at all, and
already flow into `context` unblocked. The coordinate is different: **it is
not merely undertested, it is DEC-60's own named governing rule** —
*"coordinates and geometry stay server-side; the model handles names"*
(`design/09-decision-index.md`, DEC-60(1)) — reaffirmed at DEC-63 (*"the
coordinate is resolved internally and NEVER returned"*), DEC-64 (*"coordinate
never model-facing, DEC-24/60 intact"*), and DEC-69(2), which states the
rule's actual scope in so many words: *"R-28 governs what leaves toward the
MODEL/GROUP."* A dedicated test enforces it:
`tests/test_routes.py::test_place_info_returns_the_google_rating` asserts
`"lat" not in json.dumps(out)`.

The first draft of this document proposed loosening that rule for
`place_info` specifically, reasoning that a restaurant's own already-public
pin isn't the private information R-28/DEC-60 exist to protect. That
argument may even be right on its own terms, but reopening a rule
reaffirmed four separate times, with no reply-side coordinate screen to
back it up (`nora/agent/egress.py`'s `scan_reply` never checks for a bare
coordinate — the only coordinate screens in this codebase apply to tool
*arguments*, never to a tool *result* reaching the model), is a bigger and
riskier change than this problem needs. **Withdrawn.**

**The actual fix: Smith geocodes the address itself.** `context` carries
the place's name/address/rating/price exactly as `place_info` already
returns them — genuinely no change to that tool at all. When an artifact
needs a map, Smith calls Google's Geocoding API
(`https://maps.googleapis.com/maps/api/geocode/json?address=...`) with the
address text, using the same `GOOGLE_MAPS_API_KEY` `render_map` already
receives (`nora/infra/compute/docker_backend.py`'s `_start_container`
already injects it) — a **new, small skill**, `geocode_address(address) ->
tuple[float, float]`, that only Smith's own model ever calls, inside its
own container, on data Nora was already allowed to relay. No new credential
to provision, either: `.env.example`'s own instruction for this key already
reads "Restrict the key at Google to Routes + Places + **Geocoding**" —
scoped for exactly this call before anything used it. The resulting
coordinate is model-facing *inside Smith*, but never reaches Nora's own
context or Nora's own model at any point — which is exactly the boundary
DEC-60(1)/DEC-69(2) actually draw ("toward the MODEL/GROUP" is Nora's
conversational surface; Smith's own internal render pipeline, which never
speaks to the group directly, per §3.3, is not that surface). DEC-60,
DEC-63, DEC-64, and their tests are untouched by this document — `git diff`
against `nora/infra/routes/client.py` should be empty.

This is a materially smaller change than the withdrawn version: one new
Smith-side skill, zero changes to a tested, four-times-reaffirmed rule, and
no new coordinate ever sitting in Nora's own model context to screen at
reply time.

### 3.2 Route/person facts — resolution stays with Nora, only the result crosses

The harder case, surfaced directly by this conversation: a report needing
directions for named people from their own homes to a shared destination.
`route`'s own person-name screen (R-28) is *a* place a name resolves to a
location in this codebase — not the only one, correcting the first draft:
`place_info` carries the identical screen
(`nora/agent/tools.py:2100-2117`: `scan_identifiers` +
`_looks_like_coordinate` + `_names_a_person`, tested at
`tests/test_routes.py::test_place_info_tool_refuses_a_person`). That
correction *strengthens* this section's conclusion rather than weakening
it: there is no second, unguarded path to a person's location a report could
route around `route`'s own screen through — both of this codebase's
name-resolving tools carry it. Smith has neither, and must never be given
one; see §2.

The resolution therefore happens once, on Nora's side, exactly as it does
today (`route("home", "胡同")` still runs through the same R-28 screen and
`RouteClient`, unchanged). What may cross into `context` is `route`'s own
already-typed, already-non-coordinate result — duration, distance, mode,
transit steps. This is genuinely coordinate-free by construction, not just
by convention: `ROUTES_FIELD_MASK`
(`nora/infra/routes/client.py:109-122`) requests only duration,
`distanceMeters`, and per-step transit fields (line, stop *names*, times,
headsign, stop count) — never a stop's `location`, never the polyline.
`"Ah Long: 25 min via MTR"` discloses a travel time, not where his home is.

**A gap the first draft missed, and this revision closes with an
enforcement rule that actually matches how the leak can happen — not the
one first written here.** The risk isn't `route`'s own result echoing a
resolved name back to the model (a first pass at this section assumed a
code-level filter on that result would be enough, which doesn't hold: by
DEC-64's own design, the model already holds the resolved saved-place name
*before* it ever calls `route` — `saved_places()` returns the name
directly, and the model is instructed to pass "that exact name" as the
argument. `route`'s result has nothing to leak that the model doesn't
already have from a different call entirely.) The real risk is simpler and
has to be screened where `context` itself arrives: Nora, composing the
`make_artifact` call, could type `"Miles Home"` into a `context` entry
because she has that string in hand from `saved_places()`, independent of
anything `route` returns. That is R-35/DEC-60 subset-gated data (§S.3 of
the presence design) with no subset check on this channel, headed straight
for a permanently published artifact.

**Rule, enforced at `make_artifact`'s own boundary, not by trusting the
model to follow a doctrine line:** every string value in an incoming
`context` payload is scanned with the same screens `route`/`place_info`
already apply to their own arguments — `scan_identifiers`,
`_looks_like_coordinate`, and `_names_a_person`
(`nora/agent/tools.py:1024`) — with `_names_a_person` additionally checked
against the room's own `resolvable_places()` names (DEC-64's own source for
saved-place names), not only person names. A match anywhere in `context`
refuses the whole call (`{"status": "refused", ...}`), the same shape
`route`/`place_info` already use for an identifier/person-name hit — not a
silent strip, which would build an artifact missing data nobody was told
about. No new detection logic: the three functions already exist and are
already imported into this file; `context`'s screening is one more call
site for each, not a new mechanism. A named test enforces it (§7): the free
number is
**R-43** (highest existing is R-42), and the decision-index entry gets a
`RULE_TESTS` line — CLAUDE.md's own requirement for a new behavioural rule,
which the first draft named but didn't actually add.

The general rule stands as originally stated: **anything requiring identity
or person resolution is computed by Nora, using the existing gated tools,
always; only the derived, non-identifying result — never the resolved
name — ever reaches `context`.**

### 3.3 A wrong premise, corrected

An earlier round of this conversation considered "expose more to Smith,
it's fine because Nora is the final gate" as the safety argument for a
broader loosening. It doesn't hold, and the correction matters beyond that
one exchange: `nora.domain.ports.ArtifactIngest`'s own docstring states
Smith's production caller for `register`/`update` is "Smith's own HTTP
client against `drop` directly... not `DropArtifactIngest`." Nora never
sees the artifact's bytes; she composes a summary afterward from
`result.summary`/`result.url` only. **There is no review step on what
Smith publishes.** This is precisely why §3.2's person-data boundary has to
hold regardless of what reaches Smith otherwise — a person's resolved
location, if it ever reached Smith, could land on a public, permanent,
shareable Drop URL with no gate at all. §3.1's geocoding step is safe
*because* an address was never sensitive to begin with, not because
anything downstream reviews it — the same reasoning, cited honestly this
time against a rule that's actually been checked rather than assumed
absent.

## 4. The mechanism: `context` as a parameter, delivered as a file

`make_artifact(task: str, context: list[dict] | None = None, artifact_id: str | None = None)`.

- **`context` is explicit, not auto-collected.** An earlier design in this
  conversation considered a per-turn accumulator that `place_info`/`route`
  populate automatically, removing the model from the transfer entirely.
  Rejected in favour of the simpler, explicit shape: Nora curates what's
  actually relevant to *this* artifact (she may look up six restaurants and
  use three), and the failure this document is fixing — prose compression
  losing structured fields — does not apply the same way to a model
  copying JSON fields it can already see into a new JSON array. That is a
  much more literal task than summarizing them into a sentence.
- **`| None = None`, not required.** A task with nothing gathered (a pure
  "write me a poem about X" artifact) should not force the model to invent
  an empty list on every call — the same DEC-24 shape ("a tool never asks
  the model for a value it doesn't need") this parameter is otherwise
  outside the scope of, made moot by simply not requiring it.
- **Schema-light, deliberately.** No enforced shape beyond "JSON-serializable
  list of dicts" — §2 already establishes Smith decides how to use what it's
  given; over-specifying the payload's shape here would just be a second
  place for drift between what this document says and what real place/route
  results actually look like. §3.2's route entries are the one exception,
  narrowly enforced by construction, not by a general schema.
- **Delivered as a second file, never folded into the prose.**
  `LocalDockerBackend` writes `context.json` into the job's workspace
  alongside `task.txt` (both already bind-mounted — no new mount). Smith's
  `agent.py` reads both at startup. One line added to `doctrine.md`:
  `context.json` is verified — build from it directly, never re-derive or
  guess at a fact it already gives you.
- **Not a DEC-24 regression.** DEC-24 removed parameters a tool never read
  (the dead `conversation_id` on `send_reply`/`read_buffer`) — forcing the
  model to invent a value that went nowhere. `context` is read and written
  to disk on every call that supplies it, and optional on every call that
  doesn't.
- **`ComputeBackend.launch`'s contract changes, and that's named here rather
  than glossed over.** Its docstring today reads: *"this Protocol takes only
  a string, so a caller cannot accidentally hand it anything richer"*
  (`nora/domain/ports.py:862-865`) — a designed guarantee, not an
  oversight, that this document proposes deleting. The justification is §2:
  that guarantee encoded "Smith gets nothing but a distilled brief," and
  this document's whole argument is that the brief alone lost real,
  already-typed data on the way. `launch(task: str, context: list[dict] |
  None, artifact_id: str | None) -> str` — every implementation
  (`LocalDockerBackend`, `FakeComputeBackend`, `nora/testing/eval_backends.py`
  if it constructs one) and the contract suite pinning the signature
  (`tests/contract/test_compute_backend_contract.py::test_port_methods_are_keyword_only_after_self`)
  all move together.

## 5. Traceability: hash the payload into `compute_jobs`

The workspace directory already outlives the container — confirmed
directly: `job_4e0b963a1bdf4a57bd73b5e0905fb0d9`'s `task.txt` is still on
disk; only the container itself is removed
(`LocalDockerBackend._kill_and_remove`). That is an accidental audit trail
today, not a designed one — nothing points from a `compute_jobs` row back
to it, and nothing would notice if the file on disk stopped matching what
was actually launched.

Add `task_sha256: Text not null` and `context_sha256: Text not null` to
`compute_jobs` (migration `0009` — confirmed free; `0008_compute_jobs` is
the current head). Computed by `make_artifact` itself, not
`LocalDockerBackend` — it already holds both `task` and `context` in hand
before calling `launch`, whereas `launch` returns only a bare `job_id: str`
(`ports.py:878`) with no path back to a store. `make_artifact` passes both
hashes to `ComputeJobStore.create` alongside the row it already writes on
the slow path. This mirrors the `doctrine_sha256` provenance pattern
already carried on every tag (`nora/agent/guards.py`, `nora/app/fire.py`,
`nora/obs/tracing.py` all reference it) — same idea, a new mechanism (a
persisted column, where `doctrine_sha256` today is log/trace-only, never a
Postgres column) applied to the one other place this codebase hands an LLM
a governing payload it didn't author itself.

**Named limitation, not solved here:** `ComputeJob` rows are written only
on the slow path (`nora/domain/compute.py`'s own docstring: *"the fast path
never writes a row at all"*). A job that finishes inside the ~15s sync
window — plausibly the majority, and plausibly the job this document was
written from — gets no hash, no row, nothing to check the workspace file
against. Extending provenance to fast-path jobs means writing (and later
pruning) a row for every successful call, not only the slow ones — a real
design decision with its own retention story, out of scope for this
document. Stated as a gap, not silently narrowed.

Not proposed here either: retention/cleanup of `data/compute-workspaces/`,
which accumulates one directory per job indefinitely regardless of this
document. Real, adjacent, and out of scope — flagged so it isn't lost.

## 6. Left open, on purpose

**Container scope.** Raised in this same conversation and not resolved,
and narrower than the first draft claimed: of Smith's five skills,
`render_markdown`, `render_map`, and `render_html_report` are fixed,
parameterized functions — none execute model-authored code. **`run_python`
(the charts skill) is the first exception** — no fixed-signature
`render_chart(data)` exists; `doctrine.md` has Smith adapt a gallery
template's markup to real data directly. **`render_mermaid` is a second, of
a different shape:** it shells out to `mmdc`
(`nora/compute/smith/skills/mermaid/render.py:40-58`), which launches a
Chromium instance configured with `--no-sandbox`, to parse **model-authored
diagram source** — that file's own comment names the real boundary: "the
untrusted-input containment boundary is the Docker sandbox this process
runs inside, not a check at this call site." A no-sandbox browser parsing
model-authored input is not a function that structurally needs no
isolation.

So the real split, if one is built, is three skills that don't need a
container against two that do — not four against one as first drafted.
Because both `run_python` and `render_mermaid` sit in Smith's toolbox on
every job today, not conditionally, containerization currently applies
uniformly regardless of whether a given task uses either. The fork named
earlier still stands — keep it uniform (simpler, defense-in-depth: a model
can always reach for a tool that's present) versus split Smith into a
lighter compose-only path and a full sandboxed path for chart- or
diagram-requesting tasks — but nothing in this document depends on
resolving it, and it isn't decided here.

## 7. What changes, at a glance (not a plan — the next plan is written from this)

| File | Change |
|---|---|
| `nora/domain/ports.py` | `ComputeBackend.launch` gains `context: list[dict] \| None`; its docstring's "takes only a string" guarantee is removed and replaced per §4 |
| `nora/agent/tools.py` | `make_artifact` gains `context: list[dict] \| None = None`; computes both hashes (§5); its docstring's "This is ALL the compute job will see" line is corrected |
| `nora/infra/compute/docker_backend.py` | `launch`/`_execute_job` take `context`; writes `context.json` beside `task.txt` |
| `nora/compute/smith/agent.py` | reads `context.json`; new `geocode_address` skill (§3.1) |
| `nora/compute/smith/skills/geocode/` (new) | the Geocoding API call, mirroring `skills/maps/render.py`'s shape |
| `nora/compute/smith/doctrine.md` | `context.json` is verified ground truth; when to call `geocode_address` before `render_map` |
| `nora/domain/compute.py`, `nora/infra/sql/compute_jobs.py`, `nora/testing/fakes.py` | `task_sha256`/`context_sha256` on `ComputeJob`; `FakeComputeBackend.launch`'s new `context` param |
| `nora/infra/sql/migrations/versions/0009_*.py` | the two new columns |
| `tests/contract/test_compute_job_store_contract.py` | hash fields covered like every other column |
| `tests/contract/test_compute_backend_contract.py`, `tests/test_compute_backend_port.py` | `launch`'s new signature, both places it's pinned |
| `tests/unit/test_tool_registry.py`, `tests/test_reminder_tools.py` (`make_artifact`'s own suite) | the new parameter, the route-entry construction rule (R-43) |
| `design/09-decision-index.md` | a `DEC-100`-shaped entry for §3's boundary; `RULE_TESTS["R-43"]` |
| `tests/test_acceptance_rules.py` | `R-43` entry, per CLAUDE.md's own requirement for a new behavioural rule |

Explicitly NOT changed: `nora/infra/routes/client.py` (`place_info`'s
coordinate suppression stays exactly as shipped — §3.1's revision), and
`nora/testing/eval_backends.py::CannedRouteSource` (no reason to diverge,
since `place_info`'s real behaviour isn't moving).

## 8. Open questions for review

- Exact `context.json` shape conventions beyond "list of dicts" — worth a
  light, non-enforced convention (`{"kind": "place"|"route", ...}`) purely
  for Smith's own doctrine to reason about, not a validated schema.
- Whether `task_sha256`/`context_sha256` should also ride on the eventual
  `compute_occasion` delivery, for a reader who wants to verify what was
  actually built without a DB query.
- §5's fast-path provenance gap — worth its own review before deciding
  whether it's worth closing at all.
- §6's container-scope fork is named, not decided — worth its own short
  review before a plan touches it.
- `design/09-decision-index.md`'s R-28 row (`:100`) names only
  `nora.agent.tools.route`, not `place_info`'s identical screen (shipped
  under DEC-63) — pre-existing index drift, unrelated to this document,
  worth a one-line fix in the same pass that adds DEC-100.
