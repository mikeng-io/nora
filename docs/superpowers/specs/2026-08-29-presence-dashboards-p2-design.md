---
kind: design
title: "Presence analytics P2 — web dashboards over the derived substrate"
description: "The first consumer of the presence-analytics substrate: read-only web dashboards (personal place graph, hour-of-week heatmap, route map with corridor speeds) served by the existing portal API, numbers-only, no prose, gated by the existing session auth."
tags: [nora, design, presence, analytics, web, dashboards, consumer]
timestamp: '2026-08-29'
status: proposed
---

# Presence analytics P2 — web dashboards over the derived substrate

## 0. The problem, with live evidence

The substrate (design/27, DEC-115) has been emitting typed episodes since
2026-08-20: `presence_stays`, `presence_trips`, `presence_derived_places`,
`presence_corridors`. As of 2026-08-29 the tables hold:

| table | rows | notes |
|---|---|---|
| `presence_stays` | ~300 | 8/7 → 8/19 dense (~30/day), then the issue #80 gap, resuming post-fix |
| `presence_trips` | 296+ | ~30/day since 8/20, 4 corridors linked (5.7%) |
| `presence_derived_places` | 9 | 5 people home/work; labels empty until #10 option 1's fallback fills them |
| `presence_corridors` | 4 | repeated route sequences |

Nobody reads any of it. The only presence surface in the portal today is
`GET /app/v1/presence` and `/presence/{person_id}/history` — live positions
and raw fix history for the map. The derived layer — the whole point of the
substrate — is invisible.

Issue #9's consumer table says P2 is the natural first consumer: read-only,
numbers-only, no prose, no disclosure-seam extension (a dashboard page is
not a conversation; the existing portal-wide session auth already gates it,
same as `nora/web/presence.py`'s documented decision).

## 1. Scope

Three read-only dashboard surfaces, all served by the existing portal API
(same process, same port :9100, same `requireApiSession()` gate):

1. **Personal place graph** — each circle member's derived places
   (`presence_derived_places`), with label (now populated by #10 option 1),
   kind, confidence, first/last seen, and the stays that built them.
2. **Hour-of-week heatmap** — per subject, stays bucketed by hour-of-week
   (the substrate already computes `hour_of_week` in
   `nora/domain/mobility.py`), showing routine shape.
3. **Route map with corridor speeds** — trips with their corridor linkage
   and speed stats; the map draws the trip's fix path (reusing the existing
   history endpoint's fix data) with corridor-matched trips highlighted.

**Out of scope (deliberately):**
- P1 (Nora tools) and P3 (ambient) — separate specs, P3 last by decision.
- Any write path. Dashboards read only.
- Any prose generation. The API returns numbers and coordinates; the
  frontend renders them. No LLM in the loop (design/27 §3.5: the substrate
  emits typed facts, an LLM belongs at the consumer edge — and a dashboard
  is the edge that needs no LLM at all).
- Disclosure-seam changes. Portal-wide visibility is the existing,
  operator-confirmed product decision (nora/web/presence.py docstring).

## 2. API design

Three new GET endpoints under the existing `/app/v1/` prefix, registered in
`nora/web/api.py` alongside the current routes, each returning the R-27
shape (empty list / 404 when `presence` is disabled or the subject has no
data — never an error).

### 2.1 `GET /app/v1/presence/analytics/places`

Per-subject derived places:

```json
{
  "subjects": [
    {
      "subjectId": "avery_walsh",
      "places": [
        {
          "kind": "home",
          "label": "麗靜徑 Tin Sgak Path",
          "lat": 22.4106,
          "lng": 114.1245,
          "radiusM": 120.0,
          "confidence": 0.45,
          "firstSeenTs": 1787133855000,
          "lastSeenTs": 1788015839599,
          "stayCount": 42,
          "totalDwellMs": 1782000000
        }
      ]
    }
  ]
}
```

- `stayCount` / `totalDwellMs` aggregate the stays whose centroid falls
  within the place's radius (same `_within` test the rollup uses).
- `label` is whatever `presence_derived_places.label` holds — Google-cache
  label or #10's Life360 mode fallback, or `null` (fail-closed).

### 2.2 `GET /app/v1/presence/analytics/rhythm?subjectId=<id>`

Hour-of-week heatmap for one subject:

```json
{
  "subjectId": "avery_walsh",
  "bins": [
    {"hourOfWeek": 0, "stayCount": 3, "totalDwellMs": 7200000},
    {"hourOfWeek": 1, "stayCount": 4, "totalDwellMs": 10800000}
  ],
  "windowDays": 30
}
```

- `hourOfWeek` 0–167 (Monday 00:00 → Sunday 23:00), computed from
  `started_ts` in the configured tz (HKT), matching the substrate's own
  `hour_of_week` semantics.
- `windowDays` is the query window (default 30, capped at 90).
- The frontend renders a 24×7 grid; the API stays shape-only.

### 2.3 `GET /app/v1/presence/analytics/routes?subjectId=<id>`

Trips with corridor linkage and speed stats:

```json
{
  "subjectId": "avery_walsh",
  "trips": [
    {
      "id": 293,
      "startedTs": 1788012780000,
      "endedTs": 1788013740000,
      "distanceM": 23300,
      "avgSpeedKmh": 87.2,
      "fixCount": 41,
      "corridorId": 2,
      "corridorSampleCount": 5
    }
  ],
  "corridors": [
    {"id": 2, "sampleCount": 5, "firstSeenTs": 1787900000000, "lastSeenTs": 1788013740000}
  ]
}
```

- The map layer (frontend) overlays the trip's fix path by calling the
  existing `/presence/{person_id}/history` endpoint for the trip's time
  window — no new fix-path endpoint needed.
- Corridor-matched trips carry `corridorId`; unmatched are `null`.

## 3. Data access

New read-only store methods in `nora/infra/sql/mobility.py` (the analytics
store — these tables live in the analytics schema):

- `places_with_aggregates(subject_id)` — derived places + stay aggregates.
- `rhythm_bins(subject_id, window_ms)` — stays bucketed by hour-of-week.
- `trips_with_corridors(subject_id, window_ms)` — trips joined to corridors.

All three are SELECT-only, bounded by `window_ms` / row caps (mirroring
`_MAX_HISTORY_HOURS` / `_MAX_HISTORY_POINTS` in `nora/web/presence.py`),
and share the analytics engine's pool — no new connections, no new
lifecycle (composition.py's "one pool, one lifecycle" rule).

**Wiring (the part that needs care):** the `nora` container's composition
root (`nora/app/composition.py`) does NOT currently build a
`SqlMobilityStore` — only the analytics process does. P2 needs the web
layer to read the analytics tables, so composition must gain a
`SqlMobilityStore` (or a read-only facade over it) built on the SAME
engine the record store and presence reader already share, and pass it
into `register_web_routes` alongside `presence`. This is additive — the
analytics process keeps its own store; the web process gets a second,
read-only consumer of the same tables. The `lint-imports` contract
("the analytics substrate serves no particular consumer") is one-way:
`nora.analytics`/`nora.domain.mobility` may not import the agent/app —
nothing forbids `nora.app`/`nora.web` from importing `nora.infra.sql.mobility`.
The web layer must NOT import `nora.analytics` internals (rollup,
composition) — it reads through the store only, keeping the substrate's
neutrality intact.

## 4. Frontend

The portal frontend is a separate app (design spec
`2026-08-17-nora-web-design.md`, loopback-only, session-auth'd). This spec
covers the API contract only; the frontend work (three pages: places,
rhythm heatmap, routes map) is a follow-up implementation task on the
portal side, consuming these endpoints. The API is the deliverable here.

## 5. Security & privacy

- Every endpoint sits behind `requireApiSession()` — the same
  identity.toml allowlist gate as every other portal page. No new auth
  surface.
- Portal-wide visibility (every logged-in friend sees every circle member)
  is the existing operator-confirmed decision; this spec extends it to
  derived data without changing it.
- No PII beyond what the portal already shows: derived places are
  coordinates + labels Life360 already sends; trips are corridors + speeds,
  no raw addresses in the payload (the `sequence` jsonb stays server-side
  unless a future spec argues for it).
- R-27 shape on absence: disabled presence → empty lists, never errors.

## 6. Deployment

Same process, same port: routes register onto the existing aiohttp app in
`nora/web/api.py` (the `register_web_routes` function), served by the
`nora` container on :9100. No new container, no new port, no new env.
Ships with the next `nora:latest` build.

## 7. Decisions (operator-confirmed 2026-08-29)

1. **Window default: 30 days** with a `?days=` param (capped 90). The
   substrate has ~3 weeks of data; 30 shows the routine shape, 7 is the
   portal's existing map default.
2. **Place graph scope: all circle members in one call** — matches the
   portal's group-wide model; the roster page already lists everyone.
3. **Corridor display: visible** — show corridor sample counts on the map
   legend; it's the one number that proves the substrate's corridor
   accumulation is working.

## 8. Acceptance

- Three endpoints registered, gated, R-27-shaped, bounded.
- Store methods SELECT-only, capped, sharing the analytics pool.
- `lint-imports` passes (web layer never imports analytics internals).
- Unit tests: payload shapes, empty/disabled cases, window caps, hour-of-week
  bucketing matches the substrate's own `hour_of_week`.
- Full gate green (pytest + ruff + mypy + fragments).
