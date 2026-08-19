---
kind: spec
title: "The Event module — gatherings, RSVP, and chat-derived venue recommendations"
description: "A new nora/domain/events.py: durable Event/RSVP state, a chat-first tool set that reuses the existing reminders fire path for notifications, on-demand food/venue recommendations from Honcho, and a read-only web surface. No new skills system, no new scheduling, no route recommendations yet."
tags: [nora, events, domain, tools, web, honcho, design]
timestamp: '2026-08-19'
status: proposed
---

# The Event module

## Why

Miles wants to plan gatherings (dinners, outings) with friends through Nora
instead of relying on memory: "we're having dinner Friday," someone else adds
a venue two days later, everyone gets reminded, and Nora can suggest a venue
by pulling food preferences and past venue mentions out of chat history
instead of someone re-typing them.

Three things this is not:

- **Not a scheduling primitive.** Reminders already exist
  (`nora/domain/reminders.py`) with a working fire path into chat. An event
  that has a time is a thing that OWNS a reminder — it does not reimplement
  "notify people later."
- **Not a session-state feature.** Nora holds no conversation state between
  turns — each message rebuilds a `TagContext` from a bounded transcript
  window (`nora/engagement/context.py`'s `context_window`) and nothing else
  survives in process. "Oh, we also have a venue now" arriving three days and
  fifty unrelated messages after "let's do dinner Friday" cannot be solved by
  a bigger transcript window or a skill — it has to be solved by durable,
  queryable state that a later turn can find regardless of what the
  transcript currently contains.
- **Not a new memory-extraction pipeline.** No background "Dreamer" exists
  today and this spec does not add one. Venue/food-preference recommendation
  is an on-demand read against Honcho's existing dialectic layer, which
  already distills raw ambient chat into queryable facts — the ambient
  message feed already reaches Honcho for every mapped sender
  (`nora/engagement/pipeline.py:589`), so there is no ingestion gap to close
  first.

## What this change is, and is not

**Is:** a new `nora/domain/events.py` (types + port), an `EventService`
use-case layer used by exactly one writer (the agent tools) and read by two
adapters (the agent tools, the web app), four write tools and two read
tools, a migration for two tables, and read-only `/events` web pages.

**Is not:** a scheduling system (reuses reminders), a skills system (plain
tools, same as reminders), a route/ETA engine (descoped for scope discipline,
not an infrastructure wait — see §5.1), or a background extraction job (recommendations are computed on
request, not pre-computed).

## 1. Architecture

    nora/domain/events.py        PURE: Event, RSVP, EventStatus, RSVPResponse,
                                  EventStore (Protocol port)
    nora/app/events.py           EventService — the use-case layer. Owns
                                  validation, the open-event lookup rule, and
                                  the RSVP/status state machine. The ONLY
                                  place event business rules are written.
                                  Takes the SAME route_source (PlaceResolver)
                                  nora/agent/tools.py's place_info/route
                                  already use — no second Google client.
    nora/infra/sql/events.py     Postgres EventStore
    nora/agent/tools.py          create_event / update_event / rsvp_event /
                                  cancel_event / list_events / suggest_venue —
                                  thin: parse LLM-facing args, call
                                  EventService, translate to an R-27 result.
    nora/web/api.py              GET /events, GET /events/{id} — thin: resolve
                                  person_id from session, call
                                  EventService.list_events / get_event.

This mirrors the reminders split (`nora/domain/reminders.py` +
`nora/app/fire.py`) with one deliberate addition: reminders only ever had one
adapter (chat), so its validation lives inline in the tool closure. Event has
two adapters from day one (agent tool, web read), so its rules live in
`EventService` instead, and both adapters call the same functions rather than
each re-implementing "what makes an event valid." The web adapter today only
calls the read half of `EventService`; nothing currently calls the write half
except the agent tools, and this spec does not add a web write path.

### 1.1 Import boundaries

`nora/domain/events.py` is an ordinary domain module — it is free to depend
on `nora.domain.reminders` for the linked-series relationship. It is not
under the `nora.analytics` neutrality contract (`design/proposed/27-presence-analytics.md`
§1.1) and needs no import-linter carve-out.

## 2. Data model — migration `0015_events`

(`0015_presence_analytics`, which an earlier draft of this spec assumed
would land first, has not — `0015` was free and this module took it. See
the implementation plan, `docs/superpowers/plans/2026-08-19-event-module.md`,
for the actual migration.)

    Event
      id                 UUID, pk
      group_id           text  — same group→workspace concept reminders use
      title              text
      created_by         text  — person_id
      when               timestamptz, nullable — a proposed event may have no
                          time yet ("let's do dinner" before a date is picked)
      venue_text         text, nullable — what was actually SAID in chat
                          ("that ramen place near Central"), kept as the
                          source-of-truth reference regardless of resolution
      venue_place_id     text, nullable — Google place_id, resolved ONCE
      venue_name         text, nullable — Google's canonical resolved name
      venue_address      text, nullable
      venue_rating       float, nullable
      venue_price_level  text, nullable
      venue_maps_url     text, nullable
      venue_resolved_at  timestamptz, nullable
      status             EventStatus: proposed | confirmed | cancelled
      reminder_series_id UUID, nullable — `Series.id` (the reminders table's
                          `series_id` column, per `nora/infra/sql/reminders.py`),
                          shared across every occurrence in the linked series
      created_at         timestamptz

    RSVP
      event_id     UUID, fk -> Event.id
      person_id    text
      response     RSVPResponse: yes | no | maybe
      responded_at timestamptz
      pk (event_id, person_id)

Hand-written migration, reviewed before merge (autogenerate is fine for these
two tables specifically — neither is the load-bearing
`idx_messages_conv_ts` index CLAUDE.md calls out by name).

Every datetime follows the reminders' UTC discipline
(`nora/domain/reminders.py`'s `ensure_utc`/`normalise_reminder`): naive
datetimes are rejected at the port boundary, not guessed.

### 2.1 Venue resolution — cached once, not re-queried

The `venue_*` columns are **not** raw free text plus a separate geocode —
they are resolved exactly once, through infrastructure that already ships,
not a new geocoder. `place_info` (`nora/agent/tools.py:2262`) already wraps
Google Places Text Search behind a `PlaceResolver`
(`nora/infra/routes/places.py:141`) and returns exactly this shape —
`place_id`, canonical `name`, `address`, `rating`, `price_level`,
`maps_url` — through the same `route_source` port `EventService` reuses (it
does not own a second Google client). When `update_event` settles
`venue_text`, `EventService` calls that resolver once, runs the venue
string through the same R-28 identifier/coordinate screen `place_info`
already applies, and writes the stable fields to `Event`. Reads (`get_event`,
`list_events`, the web view) never re-query Google — they read the cached
columns.

Two things this deliberately does **not** cache: raw `lat`/`lng` (DEC-24 —
never in a model-facing schema; the resolver keeps them as its own working
state and `route()` never accepts a coordinate as an argument anyway, so
`Event` has no use for them), and `open_now` (it goes stale by the hour —
caching it risks confidently telling someone a place is open after it
closed; "is it open right now" stays a fresh, rare, on-demand check, not a
per-read cost).

## 3. `EventService` — the use-case layer

    create_event(group_id, requester_person_id, title, when=None, venue_text=None) -> Event
    update_event(event_id, when=None, venue_text=None) -> Event
    rsvp(event_id, person_id, response) -> RSVP
    cancel_event(event_id) -> Event
    list_events(group_id, status=None) -> list[Event]
    get_event(event_id) -> Event | None
    suggest_venue(event_id) -> VenueSuggestions

### 3.1 The open-event doctrine

Because nothing survives between turns, a later message about an existing
event ("maybe we also have a venue") must not become a second `Event` row.
Before `create_event` is called, the calling turn is expected to call
`list_events(group_id, status="proposed")` first — this is stated directly in
the tool's docstring, the same way `remind`'s docstring steers date
resolution (`nora/agent/tools.py:2952` onward is the precedent for
doctrine-in-docstring). Behaviour:

- **Zero open events** → `create_event` proceeds.
- **One open event** → the agent is expected to call `update_event` against
  it rather than create a new one. `EventService` does not silently merge
  this itself — the model decides, from context, whether "let's also get
  ramen" is a new event or an update to the one already open, same as any
  other ambiguous-reference resolution she already does.
- **More than one open event for the group** → fail-closed on ambiguity
  (R-5's "silence beats a wrong guess," applied here): the tool should refuse
  `create_event` and ask which event is meant, never guess.

This is a genuine new behavioural rule and gets a `design/09-decision-index.md`
entry plus a named test in `tests/test_acceptance_rules.py::RULE_TESTS` at
implementation time — not decided here, flagged here so the implementation
plan doesn't drop it.

## 4. Notifications

An event with a `when` owns exactly one `Reminder`/`Series` row, created via
the existing reminder domain when `create_event`/`update_event` sets or
changes `when`. Firing is entirely the existing `nora/app/fire.py` path —
nothing new to build. Cancelling or completing an event cancels its linked
series the same way `cancel_reminder` already does. Per Miles's decision,
notifications land back in the same group chat the event lives in; there is
no separate notification channel.

## 5. Recommendations — `suggest_venue`

On-demand, not background (per Miles's decision — no new standing
subsystem):

1. Resolve the group roster via the same `identity.toml` mapping reminders
   already use.
2. For each mapped person, run one fixed-shape Honcho dialectic query:
   *"food preferences, dietary constraints, and venues previously mentioned
   positively for this person."* A fixed query shape (not a generic one) is
   what makes results comparable across people and stable across repeat
   calls — this is a prompt-shape decision inside `EventService.suggest_venue`,
   not new infrastructure.
3. **Fail-closed per person and per call.** A person with no representation
   contributes nothing (this already happens today —
   see the `peer_card` intermittently-empty behaviour) rather than a guessed
   preference. If every person comes back empty, `suggest_venue` returns
   `nothing_found`, never a hallucinated pick.
4. Rank/dedupe candidates heuristically — mention frequency plus any explicit
   positive callback ("let's go back to X"). No ML, no new scoring model.
5. Unmapped senders (not in `identity.toml`) are invisible to this query, the
   same structural limitation every other memory tool already has. Not
   solved specially for Event.

### 5.1 Reserved extension point — routes

**Correction from an earlier draft of this spec**: routing is not blocked on
the presence-analytics substrate (design/27, Tiers 5-6). That substrate is
about deriving corridors/co-location from historical GPS fixes, which is a
different concern from point-to-point routing between two named places —
`place_info` and `route()` (`nora/agent/tools.py:2085`) already ship today,
already resolve places via `PlaceResolver`, and have nothing to do with the
presence tiers.

The real constraint is **R-28**: `route()` explicitly refuses a person or a
raw coordinate as an origin/destination — "NEVER put a person where a place
goes" — and only accepts a *named* place. A saved-places layer already
exists (`saved_places()`, DEC-60) for resolving something like "home" to a
specific saved spot per room. So a per-attendee route to the venue would be:
for each RSVP'd person, check `saved_places()` for a saved "home"; if one
exists, call `route(origin=<their saved place>, destination=venue_name,
arrive_by=event.when)`; if not, that person simply gets no route — fail-closed
(R-5), never a guessed origin from a stored position.

This spec still descopes routes from v1 (matches the original decision to
ship food + venue first), but the reserved extension point is **not** a wait
for unbuilt infrastructure — it is scope discipline. `VenueSuggestions`
still carries a `route_hints` field, `None` in this version, filled by a
later revision that calls `saved_places()` + `route()` per attendee, under
the same R-28 guard those tools already enforce.

### 5.2 Future optimization, not a dependency

The backlogged per-conversation rolling summary (design-backlog issue #4,
"update-in-place, separate from the Dreamer and Honcho," referenced in
CLAUDE.md's Design backlog section) would let `suggest_venue` check a cheap
rolling summary before falling back to a full dialectic query. This spec
does not depend on #4 landing and `suggest_venue` works without it; if #4
ships first, wiring it in is a query-order change inside `EventService`, not
a redesign.

## 6. Web surface (read-only)

`app/src/app/[locale]/(portal)/events/` — list page + `[id]` detail page,
same shape as the games hub pages (`app/src/app/[locale]/(portal)/games/`).
Shows event details, per-person RSVP status, and venue suggestions once
populated. **No create/edit/RSVP forms** — per Miles's decision, creation and
all mutation happen through chat only; the web app is a pure view. Backed by
`GET /events` / `GET /events/{id}` in `nora/web/api.py`, calling
`EventService.list_events`/`get_event` directly (same read-passthrough shape
as `nora/web/games.py`), proxied through a Next.js API route that resolves
`person_id` from the NextAuth session before calling through — the same
pattern `app/src/app/api/memory/route.ts` already uses.

## 7. Skills system

Not needed. Nora already has a domain-sense skills system
(`nora/skills/loader.py`, AgentSkills.io `SKILL.md` format, authority-gated
per R-29 — operator-config only, never message-introduced). Event's logic is
mechanical enough (four write tools, a lookup-before-create rule stated in a
docstring) to live entirely in the existing tool-table pattern, same as
reminders. A `SKILL.md` playbook remains a possible future addition if
planning logic grows genuinely multi-step, but is out of scope here — and
would only ever be an optional guidance layer on top of these tools, never a
substitute for them, since a skill carries no state across turns any more
than the transcript does (§0).

## 8. Testing, migration, rollout

- **Contract tests**: `EventStore` gets a `tests/contract/` suite (fake +
  Postgres implementation against the same suite), per CLAUDE.md's "a port
  never gains a second implementation without a shared contract suite."
- **Fakes behave like the real store**: the fake `EventStore` must reproduce
  real failure/edge shapes (empty roster, ambiguous open-event set), not
  answer every call the same way.
- **Acceptance rule**: the open-event doctrine (§3.1) is a named test in
  `tests/test_acceptance_rules.py::RULE_TESTS` plus a
  `design/09-decision-index.md` entry.
- **Release fragment**: `type: "feature"` under `release-fragments/unreleased/`.
- **Gates**: `uv run pytest -q`, `uv run ruff check`, `uv run mypy` (scoped
  dirs), `uv run lint-imports` — same as every other branch.

## Out of scope

- Route/ETA recommendations (§5.1 reserves the field, does not build it).
- A web write path (§1 states this explicitly — read-only web is permanent
  for this version, not a temporary v1 simplification).
- A background/continuous preference extractor (§5 is on-demand only).
- A new skills system (§7).
- Multi-workspace / cross-platform event visibility beyond the existing
  group→workspace mapping reminders already use.
