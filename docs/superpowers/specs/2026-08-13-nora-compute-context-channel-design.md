---
kind: spec
title: "Nora Compute — the context channel: how research reaches Smith"
description: "make_artifact gains a context: list[dict] parameter carrying Nora's already-typed tool results (place/route facts) to Smith as a separate file, not flattened prose. Draws the place-vs-person trust boundary that decides what may cross that channel, corrects a wrong assumption about register_artifact's egress path, and adds hash-based provenance for the payload."
tags: [nora, spec, compute, smith, make_artifact, trust-boundary, provenance]
timestamp: '2026-08-13'
status: proposed
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
document — it is quoted in full in §1, because every claim below is
answerable directly from it rather than from a hypothetical.

This is not a Phase 5 implementation plan. It is the design the next plan
should be written from, the same relationship Phase 4's own plan had to
§4/§5 of the original spec.

## 1. The problem, grounded in a real payload

The live `task.txt` (`data/compute-workspaces/job_4e0b963a1bdf4a57bd73b5e0905fb0d9/task.txt`)
contains, for each of six venues: name, address, a Google rating, a price
band, a one-line reason, and a line reading `Maps: https://nora.example.net/i/<slug>`.

That last field is the tell. `render_map`, Smith's own skill for embedding a
map in an artifact (`nora/compute/smith/skills/maps/render.py`), takes
`locations: list[tuple[float, float]]` — raw coordinates. Nothing else. A
shortened link is not decodable into that shape by anything Smith has. So
the brief, however complete it reads as prose, structurally could not have
fed the one skill built for exactly this kind of task.

The link is not carelessness. `place_info` (`nora/infra/routes/client.py`)
resolves a place's coordinate and explicitly never returns it — "same as
`route` (DEC-24)," its own docstring says. Nora never had the coordinate to
hand over. The Maps link was the closest thing she was holding that pointed
at a location at all.

**The deeper issue `task: str` alone exposes:** Nora's own tools already
return typed data — `place_info`'s `rating`/`price_level`/`open_now` are
"not free text" by that tool's own docstring; `route`'s duration/distance/
transit fields are the unwrapped typed half of DEC-46's field mask. Folding
all of that into one prose string forces a second, lossy translation step —
structured fact to sentence — for no reason other than `make_artifact`
having nowhere else to put it. This document adds that place.

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

## 3. The trust boundary: place data crosses freely, person data never does

Two categories of fact, deliberately not one:

### 3.1 Place facts — safe to relay as-is, coordinate included

A business's name, address, rating, price band, and coordinate are already
public — the entire reason `place_info` can look them up on Google Maps.
`place_info`'s own coordinate suppression traces to a docstring comment
("same as `route`"), not to a dedicated rule or test of its own — the only
tests that actually assert "no coordinate" are `route`'s origin/destination
screen (R-28) and `test_the_maps_link_never_carries_a_resolved_coordinate`,
and both are explicitly about a *person's* location surviving a forwarded
chat message. Neither concern transfers to a restaurant's own published
pin. **This document proposes `place_info` stop stripping the coordinate**,
so it can flow into `context` and reach `render_map` directly — no
shortened link, no geocoding step invented on Smith's side to compensate.

### 3.2 Route/person facts — resolution stays with Nora, only the result crosses

The harder case, surfaced directly by this conversation: a report needing
directions for named people from their own homes to a shared destination.
`route`'s own person-name screen (R-28, `design/09-decision-index.md`) is
the *only* place in this codebase a name resolves to a location at all, and
it exists to stop exactly that resolution reaching an egress it shouldn't.
Smith has no equivalent — and must never be given one; see §2.

The resolution therefore happens once, on Nora's side, exactly as it does
today (`route("home", "胡同")` still runs through the same R-28 screen and
`RouteClient`, unchanged). What may cross into `context` is `route`'s own
**already-typed, already-non-coordinate result** — duration, distance,
mode, transit steps — the unwrapped half of DEC-46's field mask, the same
fields that were always safe to say in a reply. `"Ah Long: 25 min via
MTR"` discloses a travel time, not where his home is. The rule this
document is naming: **anything requiring identity or person resolution is
computed by Nora, using the existing gated tools, always; only the
derived, non-identifying result ever reaches `context`.** Not because
Smith is untrusted with raw data in the abstract, but because it is
architecturally incapable of doing that resolution safely at all (§2), so
there is no version of "give Smith more" that helps here — the fix has to
be Nora doing the resolution and handing over what's already safe to say.

### 3.3 A wrong premise, corrected

An earlier round of this conversation considered "expose more to Smith,
it's fine because Nora is the final gate" as the safety argument for a
broader loosening. It doesn't hold, and the correction matters beyond this
one exchange: `nora.domain.ports.ArtifactIngest`'s own docstring states
Smith's production caller for `register`/`update` is "Smith's own HTTP
client against `drop` directly... not `DropArtifactIngest`." Nora never
sees the artifact's bytes; she composes a summary afterward from
`result.summary`/`result.url` only. **There is no review step on what
Smith publishes.** This is precisely why §3.2's person-data boundary has
to hold regardless of what reaches Smith otherwise — a person's resolved
location, if it ever reached Smith, could land on a public, permanent,
shareable Drop URL with no gate at all. §3.1's looser rule for places is
safe *because* that data was never sensitive to begin with, not because
anything downstream reviews it.

## 4. The mechanism: `context` as a parameter, delivered as a file

`make_artifact(task: str, context: list[dict], artifact_id: str | None = None)`.

- **`context` is explicit, not auto-collected.** An earlier design in this
  conversation considered a per-turn accumulator that `place_info`/`route`
  populate automatically, removing the model from the transfer entirely.
  Rejected in favour of the simpler, explicit shape: Nora curates what's
  actually relevant to *this* artifact (she may look up six restaurants and
  use three), and the failure this document is fixing — prose compression
  losing structured fields — does not apply the same way to a model
  copying JSON fields it can already see into a new JSON array. That is a
  much more literal task than summarizing them into a sentence.
- **Schema-light, deliberately.** No enforced shape beyond "JSON-serializable
  list of dicts" — §2 already establishes Smith decides how to use what it's
  given; over-specifying the payload's shape here would just be a second
  place for drift between what this document says and what real place/route
  results actually look like.
- **Delivered as a second file, never folded into the prose.**
  `LocalDockerBackend` writes `context.json` into the job's workspace
  alongside `task.txt` (both already bind-mounted — no new mount). Smith's
  `agent.py` reads both at startup. One line added to `doctrine.md`:
  `context.json` is verified — build from it directly, never re-derive or
  guess at a fact it already gives you.
- **Not a DEC-24 regression.** DEC-24 removed parameters a tool never read
  (the dead `conversation_id` on `send_reply`/`read_buffer`) — forcing the
  model to invent a value that went nowhere. `context` is read and written
  to disk on every call; it costs output tokens on the one `make_artifact`
  call, not a wasted round trip.

## 5. Traceability: hash the payload into `compute_jobs`

The workspace directory already outlives the container — confirmed
directly: `job_4e0b963a1bdf4a57bd73b5e0905fb0d9`'s `task.txt` is still on
disk; only the container itself is removed
(`LocalDockerBackend._kill_and_remove`). That is an accidental audit trail
today, not a designed one — nothing points from a `compute_jobs` row back
to it, and nothing would notice if the file on disk stopped matching what
was actually launched.

Add `task_sha256: Text not null` and `context_sha256: Text not null` to
`compute_jobs` (migration `0009`, alongside the Phase 4 columns), written by
`ComputeJobStore.create` from the exact bytes handed to
`LocalDockerBackend.launch`. This mirrors the `doctrine_sha256` provenance
pattern already carried on every tag (visible on every `tag start` log
line) — same idea, applied to the one other place this codebase now hands
an LLM a governing payload it didn't author itself. It buys two things
without duplicating the payload into Postgres: a job's row can be checked
against its own workspace file for tamper-evidence, and a support question
("why did Smith build the wrong thing") is answerable by hash comparison
before anyone has to go read raw addresses and ratings out of a database
row.

Not proposed here: retention/cleanup of `data/compute-workspaces/`, which
accumulates one directory per job indefinitely. Real, adjacent, and out of
scope — flagged so it isn't lost, not solved by this document.

## 6. Left open, on purpose

**Container scope.** Raised in this same conversation and not resolved:
four of Smith's five skills (`render_markdown`, `render_mermaid`,
`render_map`, `render_html_report`) are fixed, parameterized functions —
none execute model-authored code, none structurally need a sandbox. Only
the charts skill's `run_python` does (`doctrine.md`'s own instruction:
adapt a gallery template's markup to real data, no fixed-signature
`render_chart(data)` exists). Because `run_python` sits in Smith's toolbox
on every job today, not conditionally, containerization currently applies
uniformly regardless of whether a given task needs it. A real fork exists —
keep it uniform (simpler, defense-in-depth: a model can always reach for a
tool that's present) versus split Smith into a compose-only path (no
`run_python`, no container) and a full sandboxed path for chart-requesting
tasks — but nothing in this document depends on resolving it, and it isn't
decided here.

## 7. What changes, at a glance (not a plan — the next plan is written from this)

| File | Change |
|---|---|
| `nora/infra/routes/client.py` | `place_info` stops stripping the resolved coordinate (§3.1) |
| `nora/agent/tools.py` | `make_artifact` gains `context: list[dict]` |
| `nora/infra/compute/docker_backend.py` | writes `context.json` beside `task.txt`; `launch` takes/hashes `context` |
| `nora/compute/smith/agent.py` | reads `context.json` |
| `nora/compute/smith/doctrine.md` | one instruction: `context.json` is verified, build from it directly |
| `nora/domain/compute.py`, `nora/infra/sql/compute_jobs.py`, `nora/testing/fakes.py` | `task_sha256`/`context_sha256` on `ComputeJob` |
| `nora/infra/sql/migrations/versions/0009_*.py` | the two new columns |
| `tests/contract/test_compute_job_store_contract.py` | hash fields covered like every other column |
| `tests/test_routes.py` | new coverage: `place_info` DOES return a coordinate now (inverts the old implicit assumption) |
| `design/09-decision-index.md` | a `DEC-100`-shaped entry for §3's place/person boundary — the actual behavioural rule this document adds |

## 8. Open questions for review

- Exact `context.json` shape conventions beyond "list of dicts" — worth a
  light, non-enforced convention (`{"kind": "place"|"route", ...}"`) purely
  for Smith's own doctrine to reason about, not a validated schema.
- Whether `task_sha256`/`context_sha256` should also ride on the eventual
  `compute_occasion` delivery, for a reader who wants to verify what was
  actually built without a DB query.
- §6's container-scope fork is named, not decided — worth its own short
  review before a plan touches it.
