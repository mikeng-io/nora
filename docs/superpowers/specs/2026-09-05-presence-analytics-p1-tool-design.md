---
kind: design
title: "Presence analytics P1 — where_been, a Nora tool over the derived substrate"
description: "The second consumer of the presence-analytics substrate (P2 shipped 2026-08-29): one read-only Nora tool, where_been, answering where someone has been — today, usually, repeatedly — from typed facts (day trips, derived places, hour-of-week rhythm, corridor routes) through the SAME R-31 disclosure seam where_is uses, with the unlabeled third state made unsmoothable, and no grant to the Dreamer in this build."
tags: [nora, design, presence, analytics, mobility, consumer, tool, disclosure]
timestamp: '2026-09-05'
status: proposed
---

# Presence analytics P1 — `where_been`, a Nora tool over the derived substrate

## 0. The problem, with live evidence

The substrate (design/27, DEC-115) has been emitting typed episodes since
2026-08-20. Measured live on 2026-09-05:

| table | rows | notes |
|---|---|---|
| `presence_stays` | 1,769 | ~30/day for 2+ weeks; the raw record of where people stop |
| `presence_trips` | 835 | ~30/day; 14 corridors linked |
| `presence_derived_places` | 9 | 5 people home/work/unlabeled, with confidence |
| `presence_corridors` | 14 | repeated route sequences |

P2 (web dashboards) merged 2026-08-29 (`f11c738a`): the portal now serves
`GET /app/v1/presence/analytics/{places,rhythm,routes}`. **P1 — Nora reading
the substrate herself — has not started.** Issue #9 remains open; no P1 spec
exists. Today Nora can answer «where is X right now» (`where_is`, a single
live fix with its age) but cannot answer the derived questions the substrate
exists for.

The wiring gap is already measured: `nora/app/composition.py:717` builds a
`SqlMobilityStore` on the shared engine and threads it through
`Dependencies.mobility` (composition.py:2629) — but the only consumer is the
web layer. `nora/agent/tools.py` has zero references to mobility.

Design/27 §3.5 already assigned this edge: **where an LLM legitimately
belongs is the consumer edge, not the substrate. Nora reads typed facts as
tool results and speaks them in her own voice under her existing guardrails
— that is P1, and it is just her ordinary turn.** This spec builds exactly
that, and nothing else.

## 1. The trigger questions — what the tool is FOR

The tool exists to answer a family of questions the group actually asks.
Each row is a trigger the docstring teaches; each maps to one `what` arm:

| The group asks | what arm | answer shape |
|---|---|---|
| 「X 今日去咗邊度？」「X 今日做咗啲乜？」 | `day` | that day's trips + places visited |
| 「X 琴日/前日去咗邊？」 | `day` (with `date`) | the named day's trips + places |
| 「X 今日有冇返工？」 | `day` + `places` | work-place presence that day |
| 「X 通常幾點返屋企？」「X 平日幾點起身/返工？」 | `rhythm` | hour-of-week stay counts + dwell |
| 「X 平日去開邊度？」「X 成日去邊？」 | `places` | derived places: home/work/unlabeled + confidence |
| 「你哋成日行嗰條路綫」 | `routes` | repeated trips + corridor linkage |
| 「X 做乜咁夜返？」「X 今日好唔尋常」 | `anomaly` | **FUTURE — not in this build** (§5.5) |

**The boundary with `where_is` is the docstring's first teaching.** «X 喺
邊» / «X 返咗屋企未» is a LIVE question → `where_is` (a fix with its age).
«X 今日去咗邊» / «X 通常幾點返» is a HISTORY question → `where_been`. The
pair is deliberate: `where_is` = where they ARE, `where_been` = where they
HAVE BEEN. The model must not conflate them — a rhythm answer is about the
last N days, never a claim about right now.

**The boundary with `saved_places`:** a PERSONAL name («屋企», «阿媽屋企»)
resolves through `saved_places`; a PATTERN («平日去開邊») is `where_been`.
The two answer different questions and the docstring says so.

## 2. Naming — the capability, not the mechanism

GLOSSARY's rule: **a tool's name names the capability's full scope, not the
mechanism** — `read_buffer` was misnamed (nothing is buffered), `recall` was
mislabelled (it fetched a profile, not memories). `presence_analytics`
fails the same test: *analytics* is the mechanism (the substrate's own
word), and the tool is not about analytics — it is about where someone has
been.

**`where_been`** names the capability: where a person has been — today,
usually, repeatedly. It pairs with `where_is` (where they are), and the pair
is the honest description of the presence surface: live fix vs derived
history. The `what` arms are the question families of §1, not the substrate's
table names.

## 3. Scope

One read-only tool, `where_been`, with a closed `what` vocabulary:

| `what` | store read (verified on master) | answers |
|---|---|---|
| `day` | `trips_with_corridors(subject_id, window_ms=1d)` + **NEW** `stays_for_subject_window(subject_id, window_ms)` | one day's trips + places visited |
| `places` | `places_with_aggregates(subject_id)` (mobility.py:76) | derived places: kind, label, confidence, first/last seen, stay_count, total_dwell_ms |
| `rhythm` | `rhythm_bins(subject_id, window_ms)` (mobility.py:128) | hour-of-week stay counts + dwell, 24×7 |
| `routes` | `trips_with_corridors(subject_id, window_ms)` (mobility.py:161) | trips + corridor linkage + sample counts |

**The one new store read.** `day` needs a day-scoped stays read; the store
today has `stays_for_subject` (all stays, no window) and `trips_with_corridors`
(windowed). `stays_for_subject_window` is one SELECT following the exact
`rhythm_bins` shape (mobility.py:128-159) — SELECT-only, bounded by
`_MAX_ANALYTICS_POINTS`, consumer-shaped, and allowed by the one-way
neutrality contract (agent → store is the direction P2 already established;
the substrate never imports the agent).

**Why one tool with a `what` arm, not four tools:** the disclosure gate is
ONE seam, and four tools would each need to re-enter it, quadrupling the
surface where a `refused`/`unavailable` can drift between them. The tool's
budget is a model-round-trip currency; a single `what` keeps one budget, one
registry entry, one READ_ONLY declaration. The alternative (four tools, sized
like `where_is`) was considered and rejected: it buys discoverability at the
cost of four copies of the same guard, and the tool catalog is already 40+
entries — `adding-a-new-tool-surfaces.md` counts every surface a single tool
touches; four tools quadruple that.

**Out of scope (deliberately):**
- **Anomaly** — see §5.5. The substrate scores anomalies but does not persist
  them; an `anomaly` arm needs a substrate change first. Recorded as a future
  arm, not silently dropped.
- **Any write path.** Read-only. The Dreamer is NOT granted this tool in
  this build (see §5.4) — not because the tool writes, but because derived
  presence is more sensitive than a single live fix, and a background
  process disclosing it unprompted is the P3 question, which ships last by
  decision (design/27 §4).
- **P3 ambient behaviour** — separate spec, last by decision.
- **Any new substrate surface beyond the one day-scoped SELECT.** The other
  store methods exist; no new SQL.
- **Forecasting.** Tier 4 is still gated on 4–8 weeks of episodes.
- **Tier 6 (co-location)** — speculative, and explicitly the most sensitive
  derived data (it describes relationships, not locations of one person).

## 4. Tool contract

### 4.1 Signature

```python
async def where_been(what: str, name: str | None = None, days: int = 7, date: str | None = None) -> dict
```

- `what` — `day` | `places` | `rhythm` | `routes` (closed vocabulary, same
  shape as `social_fetch`'s frozen `(platform, what)` pair, DEC-151).
- `name` — who to read about: a person in this conversation, resolved the
  way `where_is` resolves (room ∪ circle, R-47). Absent → the room's own
  people, or the caller may pass "everyone"/「大家」 when the room is a
  verified circle subset — the group asking about the group.
- `days` — window for `places`/`rhythm`/`routes`, default 7, capped at 90
  (`_MAX_ANALYTICS_WINDOW_MS`, mobility.py:47). The store already clamps.
- `date` — for `what="day"` only: `YYYY-MM-DD`, default today. Ignored
  otherwise.

### 4.2 Result contract — R-27, six states, never fewer

- `ok` — the derived facts. `kind` is `home` | `work` | `unlabeled`
  ALWAYS — the third state is a precise, load-bearing answer, not a gap
  (design/27 §3.4, §3.5).
- `refused` — a POLICY answer: someone in the room is outside the circle,
  or the room cannot be verified as a subset (R-31). Same wording shape as
  `where_is`'s `refused`; NEVER a silent omission, never converted to
  `nothing_found`.
- `not_found` — nobody in this conversation goes by that name. Never a hint
  that they exist elsewhere.
- `ambiguous` — more than one person matches; `could_be` lists them.
- `unavailable` — the room could not be read (degraded), the mobility store
  is not wired (eval/offline composition), or the read raised. An ERROR is
  never a PASS (R-27).
- `nothing_found` — the read WORKED and the subject has no derived data yet
  (a fresh member with fixes but no completed derivation). Distinct from
  `unavailable`: «there is no derived pattern» is a real answer; «I could
  not check» is not data.

### 4.3 Docstring — the trigger teaching IS the description

The docstring is the model-facing contract (DEC-150: tools are described by
their schemas; behaviour lives in conduct.md). It MUST contain:

1. **The trigger table of §1, in prose** — the question families that call
   this tool, and the explicit boundary with `where_is` («X 喺邊» → live;
   «X 今日去咗邊» → this tool) and `saved_places` (personal name → that
   tool; pattern → this one).
2. **Never smooth `unlabeled`.** «Clara seems to work in Kwun Tong» is
   the exact failure design/27 §3.4 names. If kind is `unlabeled`, say
   there's no pattern yet — confidently — and never manufacture a place from
   `label` plus an address.
3. **Labels are household names; coordinates are not speech.** `label`/
   `kind`/`confidence`/`stay_count` may enter a reply. `lat`/`lng` and
   corridor `sequence` NEVER are read aloud and NEVER pass to another tool —
   the R-28 rule `where_is` already carries (tools.py:4562,
   `_looks_like_coordinate`).
4. **Derived = history, not location.** A rhythm answer is about the last
   N days, not where someone is now. If the question is «where are they
   NOW», the tool is `where_is`, not this one.
5. **Numbers carry their window.** «過去 7 日»/«past 7 days» belongs in any
   answer that cites a stay count or dwell time; a `day` answer names the
   date it describes.

### 4.4 Registry metadata

- `NativeTool(4, read_only=True, time_shiftable=True, cacheable=True,
  source_tier="confirmed", side_effect_state="none", idempotency="idempotent",
  retry="safe", output_contract="three_state")`.

Budget 4, sized like `transit`/`place_info`: the calls are cheap local
SELECTs (no network, no live-refresh block — unlike `where_is`'s held-2
refresh cost), so the currency is the model round trip, and one question can
legitimately walk day → places in a turn. `time_shiftable=True`: derived
facts are window answers, and the docstring carries the window with them —
the FIRST derived-data tool to set it, which is the point of the field
existing.

`read_only=True` — it belongs in `READ_ONLY` in
`tests/unit/test_tool_registry.py`, never in `ACT_VERBS`.

## 5. The disclosure seam — reuse, don't re-derive

**The tool is wired THROUGH the `Whereis` seam's guard, not a new one.**
`nora/agent/whereis.py` already owns the fail-closed shape this tool needs:

- `DisclosureGuard(circle).permits(room_person_ids)` — room ⊄ circle →
  refuse (whereis.py:241-257);
- the three-way fail-closed on room availability:
  `view.available` / `view.degraded` / `view.unnamed > 0` (whereis.py:380-455);
- `location_trusted` (DEC-74/DEC-79) — the operator's own rooms' sealed-sender
  waiver, ONE room-level switch applied to every location surface
  (whereis.py:348-356);
- `circles_from_persons` — the circle roster from identity.toml
  (whereis.py:212-238);
- `check_disclosure_invariant` — the startup pre-flight (whereis.py:260-306).

The tool composes `Room` + `DisclosureGuard` + the same `location_trusted`
flag, resolves `name` the R-47 way (circle ∪ room, whereis.py:476-496), and
reads `SqlMobilityStore` only AFTER the guard passes. This is the same
«refuse, don't omit» seam, not a sibling with weaker semantics.

**This is a deliberate asymmetry with P2.** P2's portal-wide visibility
(«every logged-in friend sees every circle member», nora/web/presence.py)
was an operator-confirmed product decision for the web app — session-auth'd,
human-clicked, behind the portal's own gate. A Nora REPLY into a room
conversation is speech, not a click, and has a different audience model: the
room. The P2 decision therefore does NOT extend here. The tool refuses into
any room that fails the subset test, exactly as `where_is` does.

## 6. Wiring

- `build_tool_specs` gains `mobility=None` (the honest un-wired state; R-5)
  and threads it into `_spec("where_been", where_been)`. `None` →
  `unavailable`, never a crash, never `nothing_found` — the same shape every
  other optional collaborator already has (reminders, notes, compute_backend).
- `ToolRegistry` holds `mobility` the way it holds the presence reader +
  circles today (registry.py:227-234, DEC-59's seam).
- `nora/app/composition.py` passes the EXISTING `SqlMobilityStore` it
  already builds (composition.py:717, `Dependencies.mobility` at 2629) —
  no second store, no new lifecycle («one pool, one lifecycle»).
- The neutrality contract is untouched: `nora.agent` may import
  `nora.infra.sql.mobility` (one-way; P2 spec §3 already established the
  direction), and the tool reads through the store only — never
  `nora.analytics` internals.

## 7. Registration surfaces — the pinned checklist

Work the tool through every item in
`references/adding-a-new-tool-surfaces.md` before declaring done:

| Surface | File | Change |
|---|---|---|
| Tool wiring | `nora/agent/tools.py` | `where_been` in `build_tool_specs`, the tool fn itself, `mobility` closure threading |
| READ_ONLY | `tests/unit/test_tool_registry.py` | NOT in ACT_VERBS; add to `READ_ONLY` |
| Budgets table | `tests/unit/test_tool_registry.py` | `"where_been": 4` in the pinned budget dict |
| Exposed-tool set | `tests/unit/test_tool_registry.py` + `tests/test_tools.py` | add to expected built-tools set |
| ordinary_english | `tests/test_prefill.py` | the header catalogue may need the word for a legitimate sentence |
| DEC-150 gate | doctrine | conduct.md must NOT name the tool; behaviour layer only (see §7.3) |
| Doctrine version | `nora/doctrine/conduct.md` + `tests/test_doctrine_version_parity.py` | bump `version:` 22→23 AND the pinned sha — ONLY IF a teaching block is added (see §7.3) |
| RUF001 | `evals/**` | journeys use halfwidth punctuation |
| Output contract | `nora/agent/ports.py` + `tests/test_output_contracts.py` | `three_state` declaration, per DEC-153's declared-contract discipline |

### 7.1 Tests to write

- The guard fires identically to `where_is`: refuse on room ⊄ circle,
  refuse on `unnamed`, `unavailable` on degraded, waiver counted under
  `location_trusted` (reuse the where_is test shape, not a weaker copy).
- `unlabeled` renders as `unlabeled` end-to-end — a fixture place with kind
  `unlabeled` must come back as `unlabeled`.
- R-28: a crafted reply naming a coordinate is refused by the existing
  identifier scan — no new hole.
- `what` vocabulary is closed: any other value → a single `unavailable`
  answer (fail-closed), never a silent `{}`.
- `days` clamping: 0 → empty window behaviour, 1000 → capped at 90.
- `date` is honoured for `day` and ignored otherwise.
- Un-wired (`mobility=None`) → `unavailable`, not `nothing_found`.
- Classification: `read_only_tools()` includes it; ACT_VERBS does not.

### 7.2 Live verification (post-merge)

1. `make build` → `docker compose up -d --force-recreate nora presence
   analytics` (THREE services share `nora:latest` — recreating only nora
   leaves the others on the old image).
2. health `http://127.0.0.1:9100/health` → `connected: true`.
3. Tag Nora in the circle's room with a derived question («我哋通常幾點
   返屋企» / «Miles 今日去咗邊» / «Miles 平日去開邊»). The reply must carry
   the window, never a coordinate, and must not smooth any `unlabeled`
   place.
4. `docker exec nora head -8 /app/nora/doctrine/conduct.md` → expect the
   bumped `version:` line.

### 7.3 DEC-150 gate — the teaching belongs in the TOOL, not the doctrine

conduct.md's `not_here` already states it: «No tool catalogue. What she can
reach for is described by the tool schemas.» A conduct block that says «use
`where_been` when…» will fail the gate and must be reworked to the behaviour
layer: «When someone asks about a PATTERN — usual times, frequent places,
repeated routes, a day's whereabouts — answer from what the household's
sharing history shows, and never present a pattern as a live location.» If
such a teaching block is warranted, it is the behaviour version, and it DOES
bump the doctrine version + pinned sha. If the docstring alone carries it
(the preferred shape — the rule belongs next to the contract), no doctrine
bump is needed for this tool.

### 7.4 Dreamer — explicitly NOT granted

`read_only=True` makes `where_been` *eligible* for the Dreamer's
`WORKSPACE_SAFE_TOOLS` (as `get_knowledge`/`search_memory_workspace` are
today), but eligibility is not a grant. A background process that can fold
derived presence into her standing model — or worse, quote it unprompted
into a room — is the P3 consumer's question. It stays ungranted; granting it
is a separate operator decision with its own disclosure analysis, and this
spec records that it was NOT made.

### 7.5 Anomaly — a future arm, gated on substrate persistence

The substrate ALREADY scores anomalies: `robust_z_score` (mobility.py:87,
Tier 3, issue #6) measures how anomalous a stay duration is against the
subject's own history, and `rollup.py:1074-1081` fires
`nora_analytics_anomalies_total` when `|z| >= anomaly_z_threshold`. **But the
score is metric-only — it is never persisted.** There is no
`presence_anomalies` table, and the store has no anomaly read. The trigger
questions exist («X 做乜咁夜返？», «X 今日好唔尋常」), but an `anomaly` arm
cannot read what the substrate does not write.

**Therefore anomaly is OUT of this build, recorded as a future arm** — not
silently dropped. It becomes buildable when the substrate persists anomaly
events (a table + a rollup write + a store read, mirroring the
`presence_occupancy`/`presence_place_rhythm` pattern at schema.py:282-320),
and that substrate change is a separate spec with its own acceptance
criteria. When it lands, `where_been(what="anomaly")` is one new arm in the
same closed vocabulary, through the same guard — the tool's shape does not
change.

## 8. Acceptance criteria

1. **`where_been` is a native tool** in the tag path, registered across all
   §7 surfaces; the suite is green on the worktree and again on master's
   venv (`/home/norty/workspace/nora/.venv/bin/python -m pytest` — worktrees
   have no `.venv`).
2. **Guard parity with `where_is`:** the identical room/circle failure
   matrix produces `refused`/`unavailable` (never `nothing_found`, never
   omission); `location_trusted` waives exactly what it waives for
   `where_is`, and the waiver is counted.
3. **`unlabeled` survives end-to-end.** A fixture `unlabeled` place returns
   as `unlabeled`; the model-facing contract leaves no path to smooth it.
4. **No coordinate egress.** A reply containing a raw coordinate is refused
   by the existing R-28 scan; the docstring teaches the model the same rule
   `where_is` teaches.
5. **Un-wired compositions degrade honestly.** `mobility=None` →
   `unavailable`, exactly like `reminders=None` today.
6. **Dreamer unchanged:** `where_been` is absent from `WORKSPACE_SAFE_TOOLS`
   and from every dream adaptor's tool set (`verify_tool_subsets` stays
   green).
7. **Live-verified** per §7.2 with a real tag in the circle's room.

## 9. Decision-index entry

**DEC-155 — `where_been`: one read-only Nora tool reads the derived
substrate through the `where_is` disclosure seam, `unlabeled` intact, not
granted to the Dreamer.** P1 of issue #9. Named for the capability (where
someone has been — today, usually, repeatedly), not the mechanism
(`presence_analytics` fails GLOSSARY's naming rule; `where_been` pairs with
`where_is`). One tool with a closed `what ∈ {day, places, rhythm, routes}`
vocabulary mapping onto existing `SqlMobilityStore` SELECTs plus one new
day-scoped stays read — not four tools, so the R-31 guard is one seam with
one drift surface. The tool composes `Room` + `DisclosureGuard` +
`location_trusted` exactly as `Whereis.locate` does and resolves names the
R-47 way; the P2 «portal-wide visibility» decision does NOT extend to room
speech — a reply is not a click, and `refused` stays possible. `unlabeled`
is a load-bearing third state: the docstring forbids smoothing it into prose
(design/27 §3.4). Coordinates and corridor sequences never enter replies and
never pass to other tools (R-28, the same rule `where_is` carries).
`read_only=True` makes it Dreamer-ELIGIBLE but it is NOT granted: a
background process folding or quoting derived presence is the P3 question,
which ships last by decision. **Anomaly is a future arm, gated on substrate
persistence** — the substrate scores anomalies but never writes them
(metric-only, rollup.py:1074-1081), so an `anomaly` arm needs a table +
rollup write + store read first; recorded here so the trigger questions
(«X 做乜咁夜返？») are not lost. | acceptance criteria above +
`tests/unit/test_tool_registry.py` (READ_ONLY + budgets), new `where_been`
guard/unlabeled/R-28 tests, `verify_tool_subsets` green with the tool
ungranted.
