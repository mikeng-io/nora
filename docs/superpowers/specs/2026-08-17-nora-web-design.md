# Nora web portal — design

Status: draft, awaiting user review.

## 1. Purpose

A private website for Miles's friend group, gated to the same people already
mapped in `identity.toml`. Framed as **Nora's public face**: a status/identity
page is the primary surface, with a **memory showcase** (what Nora has learned
about each friend, via Honcho — never named as such in UI copy) as a
supporting feature.

The site is a **portal shell** over a registry of **modules**. v1 ships two
modules (status, memory). A third (game hub, migrated from the
`game-server-monitor` repo's `web/`) is designed here but built after v1. A
fourth (a Life360-derived presence/history map, "in future") is named as a
placeholder only — not designed in this document.

This is the reasoning why: Miles asked mid-brainstorm to make the portal
"an adoptor or abstracted" pattern rather than hardcoding each section, since
more modules are coming later and each has a different upstream (Nora itself,
Pulse's API, eventually a presence/ES-backed map). One shell, one auth gate,
one nav — modules are the only thing that varies.

## 2. Non-goals (v1)

- Game hub is **designed** in §5.3 but **not built** in v1 — ships as its own
  follow-up once the status + memory modules are live.
- No WebSocket / push transport. Every module is REST + client-side polling
  where "live" data is needed. (Revisit only if a future module has a genuine
  sub-second-latency requirement a poll can't satisfy.)
- No Life360 / presence history map — noted as a future module, not designed.
- No per-person opt-in/opt-out for the memory showcase. Every whitelisted
  person's full Honcho representation is visible to every other whitelisted
  person. This is a **deliberate** choice (trusted friend group, not a public
  surface), made explicitly during brainstorming — not an oversight. Revisit
  if the friend group ever grows past "people who'd already see this in the
  Signal group."

## 3. Architecture

```
web/                          Next.js 15 (App Router, RSC), React 19, Tailwind 4
  ├─ auth: Auth.js, Discord OAuth provider only
  ├─ middleware: session → identity.toml person_id lookup → allow/403
  └─ modules/
       ├─ status/             calls nora's new web API
       ├─ memory/             calls nora's new web API
       └─ game-hub/           calls Pulse's existing API directly (v2)

nora/web/                     new Python package inside this repo
  └─ api.py                   aiohttp routes, same process/container as Nora
       GET /web/v1/status     health, uptime, recent-activity summary
       GET /web/v1/memory     every whitelisted person's Honcho representation
```

`web/` is a **new sibling top-level directory in this repo**, not a separate
repo — single git history, single deploy change, matches how
`game-server-monitor` structured `monitor/` + `mcp/` + `web/` together.

### 3.1 Module registry (the "adaptor" pattern)

Each module is a self-contained unit on both sides:

- **Frontend**: a route segment under `web/src/app/[locale]/(portal)/<module>/`
  plus a nav entry declared in one place (`web/src/lib/modules.ts` — id, label,
  icon, href, and whether it's "live" or a placeholder link).
- **Backend** (only for modules Nora itself serves data for): a route
  registered in `nora/web/api.py`, each in its own file
  (`nora/web/status.py`, `nora/web/memory.py`), sharing nothing but the
  aiohttp app and the injected collaborators (`HonchoClient`, `SqlRecordStore`)
  — the same DI shape `nora/obs/health.py::build_health_app` already uses.

A module that talks to a **different** upstream (game hub → Pulse's API,
future map → wherever that lives) never touches `nora/web/`; it's purely a
frontend module with its own fetch client under `web/src/lib/<module>-api.ts`.
This is what keeps Nora's Python side untouched by game-server or presence
concerns — the portal aggregates in the browser/RSC layer, not the backend.
Adding a module later means: one nav entry, one route folder, one fetch
client (and, only if Nora is the data source, one backend route file). No
shared module touches another module's code.

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

`GET /web/v1/status` — process uptime, `/health`'s existing signals
(store/Honcho reachability), a short recent-activity summary (e.g. last N
audit rows' timestamps only, no message content). Client polls every 15-30s.
No new data source — this is a thin, filtered re-projection of what
`nora/obs/health.py::build_health_app` already computes, not a new
capability.

### 5.2 Memory showcase

`GET /web/v1/memory` — for every `person_id` in `identity.toml`, the current
Honcho representation via `HonchoClient` (same client Nora's engagement path
already uses at `nora/engagement/context.py::TranscriptInjection.assemble`).
Returned as one array, each entry `{person_id, display_name, representation}`.
No filtering beyond what Honcho itself already produced (§2 — deliberate).
UI copy: "what Nora remembers" / "her read on the group" — never the word
"Honcho".

### 5.3 Game hub (designed now, built in v2)

Migrates `game-server-monitor/web/`'s pages
(`server-card`, `player-list`, `activity-timeline`, `status-charts`, etc.)
into `web/src/app/[locale]/(portal)/games/` as a module. Fetches Pulse's
existing HTTP API **directly**:

```
GET /v1/servers
GET /v1/servers/:id/status
GET /v1/servers/:id/graph?hours=24
```

...over the tailnet, the same access pattern Pulse's own README already
documents (`tailscale serve --bg --https=443 http://127.0.0.1:8788` bridges
its loopback-only port). This repo's `web/` container reaches it as a tailnet
peer, not through Nora. Nora's `nora/web/api.py` gains no game-server code —
consistent with Nora's MCP integration with Pulse already being a *read-only,
separate* integration (per Pulse's README: Nora reads Pulse over MCP, and
`mcp/` is Pulse's least-privileged service, no Discord/game-save access).

Player/activity/session views were previously public in Pulse's own app (its
`middleware.ts` comment: "every route is public... exposes only in-game
facts"). Behind this portal's login they become friend-group-only instead —
strictly narrower than before, no new exposure.

### 5.4 Future: Life360 history map (placeholder only)

Not designed here. When it exists, it becomes a fourth module: one nav entry
in `modules.ts`, its own route folder, its own fetch client against whatever
backend that spike produces (mentioned as possibly ES-backed). No portal
shell or auth changes anticipated — this is exactly the case the module
registry in §3.1 exists for.

## 6. Deployment

New `web` service in `docker-compose.yml`, following the pattern already
established for `nora` itself: bind to `127.0.0.1` only, no public port.
`nora/web/api.py`'s routes are added to the existing aiohttp app Nora already
runs on `:9100` loopback (`ports: ["127.0.0.1:9100:9100"]` in
`docker-compose.yml`) — no new port, no new container for the backend half.
`web/` (Next.js) is its own new container/service, reaching `nora`'s `:9100`
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
  site is Nora's public face first, other modules are sections under her).
  No inbound port opened on the host; Auth.js still owns the actual login
  decision behind it, same as if it were exposed any other way.

`cloudflared` points at `web`'s container port (not `nora`'s :9100 directly —
that stays internal to the `nora` compose network, reached only by `web`
server-side).

## 7. Testing

- `nora/web/api.py` routes: same pattern as existing `nora/obs/health.py`
  tests — injected fake `HonchoClient`/`SqlRecordStore`, asserting on
  response shape, not a live dependency (per CLAUDE.md's fake-behaves-like-
  real-server rule).
- `web/`: component tests for each module in isolation (module boundary
  means a game-hub test never needs Nora's API mocked, and vice versa);
  middleware auth test using a fixture `identity.toml` snippet, asserting
  match/no-match against a fake Discord snowflake.
- No live-server test for Pulse's API — the game-hub module treats Pulse's
  API the same way Nora's own MCP tools already do: read-only, best-effort,
  never load-bearing for anything else on the portal.

## 8. Open questions for follow-up (not blocking v1)

- Exact visual design/branding of the portal — not addressed here, follows
  once the plan is written.
- Whether `/web/v1/memory` needs pagination once the friend group grows
  large enough that loading every representation on one page is heavy —
  not a v1 concern at current group size.
