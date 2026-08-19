# Nora web portal — design

Status: draft, awaiting user review.

## 1. Purpose

A private website for Miles's friend group, gated to the same people already
mapped in `identity.toml`. Framed as **Nora's public face**: a status/identity
page is the primary surface, with a **memory showcase** (what Nora has learned
about each friend, via Honcho — never named as such in UI copy) as a
supporting feature. Built as an installable, responsive **PWA** — see §3.2.

The site is a **portal shell** over a registry of **modules**. v1 ships two
modules (status, memory). A third (game hub, migrated from the
`game-server-monitor` repo's `web/`) is designed here but built after v1. Two
more are named as placeholders only, not designed in this document: a
Life360-derived presence/history map, and an interactive 1-on-1 chat with
Nora directly through the portal.

This is the reasoning why: Miles asked mid-brainstorm to make the portal
"an adoptor or abstracted" pattern rather than hardcoding each section, since
more modules are coming later and each has a different upstream (Nora itself,
Pulse's API, eventually a presence/ES-backed map). One shell, one auth gate,
one nav — modules are the only thing that varies.

## 2. Non-goals (v1)

- Game hub is **designed** in §5.3 but **not built** in v1 — ships as its own
  follow-up once the status + memory modules are live.
- No WebSocket / push transport. Every module is REST + client-side polling
  where "live" data is needed. The clearest future trigger to revisit this is
  §5.5's interactive chat module — genuine bidirectional, low-latency
  conversation is exactly the case a poll can't satisfy — but that module
  isn't designed yet, so v1 stays REST-only.
- No Life360 / presence history map, no interactive chat — both noted as
  future modules (§5.4, §5.5), neither designed here.
- ~~No per-person opt-in/opt-out for the memory showcase. Every whitelisted
  person's full Honcho representation is visible to every other whitelisted
  person.~~ **REVERSED (operator, 2026-08-19):** the memory page is now
  user-scoped — `GET /app/v1/memory` requires the caller's own `person_id`
  and returns only their own entry, fail-closed to `[]` on a missing or
  unrecognized one (`nora/web/memory.py`'s module docstring carries the full
  reasoning). The v1 choice above was made explicitly during brainstorming,
  not an oversight, but it did not survive contact with actual use.

## 3. Architecture

```
app/                          Next.js 15 (App Router, RSC), React 19, Tailwind 4
  ├─ auth: Auth.js, Discord OAuth provider only
  ├─ middleware: session → identity.toml person_id lookup → allow/403
  ├─ manifest.json + service worker (PWA, §3.2)
  └─ modules/
       ├─ status/             calls nora's new web API
       ├─ memory/             calls nora's new web API
       └─ game-hub/           calls Pulse's existing API directly (v2)

nora/web/                     new Python package inside this repo
  └─ api.py                   aiohttp routes, same process/container as Nora
       GET /app/v1/status     store/Honcho reachability, uptime, upcoming reminders
       GET /app/v1/memory     every whitelisted person's Honcho representation
```

`app/` is a **new sibling top-level directory in this repo**, not a separate
repo — single git history, single deploy change, matches how
`game-server-monitor` structured `monitor/` + `mcp/` + `web/` together.

**Naming note:** the frontend directory (`app/`) and its API route prefix
(`/app/v1/*`) both say "app," but the *Python* package stays named
`nora/web/` rather than `nora/app/` — this repo already has a
`nora/app/` package (the composition root, `nora/__main__`'s
`Dependencies`/`build_dependencies`, per `ARCHITECTURE.md`'s package table).
Renaming the Python side to match would collide with that existing package.
`nora/web/` is purely an internal implementation detail; nothing user-facing
reads that name — only the route prefix and the frontend directory do, and
both consistently say "app."

### 3.1 Module registry (the "adaptor" pattern)

Each module is a self-contained unit on both sides:

- **Frontend**: a route segment under `app/src/app/[locale]/(portal)/<module>/`
  plus a nav entry declared in one place (`app/src/lib/modules.ts` — id, label,
  icon, href, and whether it's "live" or a placeholder link).
- **Backend** (only for modules Nora itself serves data for): a route
  registered in `nora/web/api.py`, each in its own file
  (`nora/web/status.py`, `nora/web/memory.py`), sharing nothing but the
  aiohttp app and the injected collaborators (`HonchoClient`, `SqlRecordStore`,
  `SqlReminderStore`) — the same DI shape `nora/obs/health.py::build_health_app`
  already uses.

A module that talks to a **different** upstream (game hub → Pulse's API,
future map → wherever that lives) never touches `nora/web/`; it's purely a
frontend module with its own fetch client under `app/src/lib/<module>-api.ts`.
This is what keeps Nora's Python side untouched by game-server or presence
concerns — the portal aggregates in the browser/RSC layer, not the backend.
Adding a module later means: one nav entry, one route folder, one fetch
client (and, only if Nora is the data source, one backend route file). No
shared module touches another module's code.

### 3.2 PWA & responsiveness

Installable: a `manifest.json` (name, icons, `display: standalone`) and a
minimal service worker (asset caching for fast repeat loads — no offline
data story in v1, since every module is a live fetch against Nora/Pulse and
there's nothing meaningful to show offline). Responsive down to mobile
width as a baseline requirement for every module, not an afterthought —
Tailwind's breakpoint system covers this; no separate mobile app or
mobile-specific routes.

### 3.3 Frontend/backend type safety

No OpenAPI/Swagger. `aiohttp` has no built-in schema generation (unlike
FastAPI), and with two endpoints in v1 the tooling overhead isn't justified.
Pulse's own `web/` already solved this exact problem without OpenAPI —
hand-written zod schemas (`src/lib/pulse-schemas.ts`) parsed at the fetch
boundary. `app/` follows the same precedent: zod schemas in
`app/src/lib/<module>-schemas.ts` mirroring each Nora/Pulse response shape.
Revisit only if the API surface grows past what's comfortable to hand-write.

## 4. Auth

**Auth.js (NextAuth), Discord provider only**, app-level — not Cloudflare
Access. Pulse's `web/` used to sit behind Cloudflare Access and later dropped
it once its data became fully public; we're choosing the opposite of that
history on purpose, because Cloudflare Access means the deploy only works
with Cloudflare in front of it — another external dependency, inconsistent
with how the rest of this stack is self-hosted (OmniRoute, signal-cli,
Honcho all run on Miles's own hardware).

Flow:

1. Visitor hits any portal route → Next.js middleware checks for a valid
   Auth.js session cookie. None → redirect to `/login`.
2. `/login` → "Sign in with Discord" → Discord OAuth → Auth.js issues a
   session JWT containing the Discord snowflake (`user_id`).
3. Middleware resolves that snowflake against `identity.toml`'s
   `[[persons.<id>.discord]].user_id` (already a supported field —
   `design/10-identity-and-policy-schema.md` §"discord | user_id | all").
   No match → 403, session destroyed. Match → request proceeds, `person_id`
   attached to the request context for downstream use (e.g., highlighting
   "your" card in the memory module).
4. `identity.toml` is loaded once at startup (existing constraint, §"The
   three config files" in CLAUDE.md) — adding a friend's Discord id still
   requires a restart, same as today for any other platform.

No new credential material needed beyond a Discord OAuth app (client
id/secret) and an Auth.js session secret, both env-injected the same way
`OMNIROUTE_API_KEY`/`POSTGRES_PASSWORD` already are in `docker-compose.yml`.

## 5. Modules

### 5.1 Status ("Nora's public face")

`GET /app/v1/status` — process uptime, `/health`'s existing signals
(store/Honcho reachability), and upcoming/recent reminders via
`nora/infra/sql/reminders.py::SqlReminderStore` — due times and count only,
never a reminder's own text, consistent with the CLI's existing read-only
stance on reminders (`nora reminders list`; there is deliberately no
`nora reminders cancel`). A cross-room "recent activity" summary was
considered and dropped: `RecordStore.recent()` is scoped to one `conversation_id`
(`nora/domain/ports.py:656`), and there is no port method spanning every room
Nora is in — adding one is a real port
change (both `SqlRecordStore` and `FakeRecordStore`, plus a contract test),
not something this endpoint should assume into existence. Revisit as its own
task if it's wanted later. Client polls every 15-30s. Nothing here is a new
data source — status is a thin, filtered re-projection of what
`nora/obs/health.py::build_health_app`
and the reminders store already compute, not a new capability.

### 5.2 Memory showcase

`GET /app/v1/memory` — for every `person_id` in `identity.toml`, the current
Honcho representation via `HonchoClient` (same client Nora's engagement path
already uses at `nora/engagement/context.py::TranscriptInjection.assemble`).
Returned as one array, each entry `{person_id, display_name, representation}`.
No filtering beyond what Honcho itself already produced (§2 — deliberate).
UI copy: "what Nora remembers" / "her read on the group" — never the word
"Honcho".

### 5.3 Game hub (designed now, built in v2)

Migrates `game-server-monitor/web/`'s pages
(`server-card`, `player-list`, `activity-timeline`, `status-charts`, etc.)
into `app/src/app/[locale]/(portal)/games/` as a module. Fetches Pulse's
existing HTTP API **directly**:

```
GET /v1/servers
GET /v1/servers/:id/status
GET /v1/servers/:id/graph?hours=24
```

...over the tailnet, the same access pattern Pulse's own README already
documents (`tailscale serve --bg --https=443 http://127.0.0.1:8788` bridges
its loopback-only port). This repo's `app/` container reaches it as a tailnet
peer, not through Nora. Nora's `nora/web/api.py` gains no game-server code —
consistent with Nora's MCP integration with Pulse already being a *read-only,
separate* integration (per Pulse's README: Nora reads Pulse over MCP, and
`mcp/` is Pulse's least-privileged service, no Discord/game-save access).

Player/activity/session views were previously public in Pulse's own app (its
`middleware.ts` comment: "every route is public... exposes only in-game
facts"). Behind this portal's login they become friend-group-only instead —
strictly narrower than before, no new exposure.

### 5.4 Future: Life360 history map (placeholder only)

Not designed here. When it exists, it becomes another module: one nav entry
in `modules.ts`, its own route folder, its own fetch client against whatever
backend that spike produces (mentioned as possibly ES-backed). No portal
shell or auth changes anticipated — this is exactly the case the module
registry in §3.1 exists for.

### 5.5 Future: interactive 1-on-1 chat (placeholder only)

Not designed here — still an unformed idea as of this brainstorm ("still
unclear, just a thought"). Named now because it's the most likely trigger for
revisiting §2's no-WebSocket decision: a real-time chat with Nora through the
portal is a genuine bidirectional, low-latency case, unlike status/memory's
periodic-refresh needs. When this gets designed, it should get its own
brainstorm — it touches auth-scoped identity (whose chat is it, and does it
reuse Nora's existing per-person engagement path or need a new one?),
which the placeholder modules in §5.4 don't.

## 6. Deployment

New `app` service in `docker-compose.yml`, following the pattern already
established for `nora` itself: bind to `127.0.0.1` only, no public port.
`nora/web/api.py`'s routes are added to the existing aiohttp app Nora already
runs on `:9100` loopback (`ports: ["127.0.0.1:9100:9100"]` in
`docker-compose.yml`) — no new port, no new container for the backend half.
`app/` (Next.js) is its own new container/service, reaching `nora`'s `:9100`
via the existing `nora` compose network (same way `presence` and other
co-networked containers reach it today) and reaching Pulse over the tailnet
(the way this host already reaches it for the game-hub module — no change to
Pulse's own exposure).

**Public exposure is Cloudflare Tunnel (`cloudflared`), not Tailscale, and not
Cloudflare Access.** These are three separate Cloudflare-adjacent decisions
and easy to conflate:

- **Tailscale** — how *this host* reaches Pulse (already true today, unrelated
  to how friends reach the portal). Not suitable for friend-facing exposure:
  it would require every friend to install a Tailscale client just to view a
  webpage, a much bigger ask than an OAuth click.
- **Cloudflare Access** — ruled out in §4. An auth *product*; not used here.
- **Cloudflare Tunnel** — pure exposure, no auth logic of its own:
  `cloudflared` runs as a container/process on the host, opens an outbound
  connection to Cloudflare's edge, and terminates public HTTPS at
  **`nora.67.zone`** (chosen over `app.67.zone` to match §1's framing — the
  site is Nora's public face first, other modules are sections under her; the
  `/app/v1/*` route prefix and `app/` directory name are unrelated to this —
  naming the frontend "app" and naming the *hostname* "nora" answer two
  different questions).
  No inbound port opened on the host; Auth.js still owns the actual login
  decision behind it, same as if it were exposed any other way.

`cloudflared` points at `app`'s container port (not `nora`'s :9100 directly —
that stays internal to the `nora` compose network, reached only by `app`
server-side).

## 7. Testing

- `nora/web/api.py` routes: same pattern as existing `nora/obs/health.py`
  tests — injected fake `HonchoClient`/`SqlRecordStore`/`SqlReminderStore`,
  asserting on response shape, not a live dependency (per CLAUDE.md's
  fake-behaves-like-real-server rule).
- `app/`: component tests for each module in isolation (module boundary
  means a game-hub test never needs Nora's API mocked, and vice versa);
  middleware auth test using a fixture `identity.toml` snippet, asserting
  match/no-match against a fake Discord snowflake; a PWA smoke test that
  `manifest.json` is served and valid.
- No live-server test for Pulse's API — the game-hub module treats Pulse's
  API the same way Nora's own MCP tools already do: read-only, best-effort,
  never load-bearing for anything else on the portal.

## 8. Open questions for follow-up (not blocking v1)

- Exact visual design/branding of the portal — not addressed here, follows
  once the plan is written.
- Whether `/app/v1/memory` needs pagination once the friend group grows
  large enough that loading every representation on one page is heavy —
  not a v1 concern at current group size.
- §5.5's interactive chat module — genuinely unformed; needs its own
  brainstorm before design, not folded into this spec.
