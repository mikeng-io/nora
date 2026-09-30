# app — the portal

The circle's side of Nora: where everyone is, what is planned, the game
servers, and Nora herself (whether she is listening, what she remembers about
you, how she behaves, what she can do). Behind a Discord sign-in, open only to
people in `identity.toml`. An installable PWA for phones and desktops.

The public page at `/` and `/en/` is a different thing: `site/`. They share
the hostname; the site owns exactly `/`, `/en/`, `/_astro/`, `/_site/`,
`/favicon.ico`, `/robots.txt`, `/sitemap.xml` and `/llms.txt` (site/README.md,
"Going live"), so no portal page lives at a bare locale root, and the portal's
own files sit under paths the site does not claim (`/sw.js`,
`/manifest.webmanifest`, `/offline.html`, `/icons/`, `/vendor/`,
`/screenshots/`).

## What is where

| route | what it is |
|---|---|
| `/today` | the front page: Nora's state, who is around, who is playing, what is coming up, a line from her notes about you, the circle |
| `/map` | everyone on a live map; a person's detail and route window; `?person=` and `?trail=` in the URL |
| `/people`, `/people/<id>` | the roster; a person with their last day, plans you share, game profiles, and three pattern tabs: places, a usual week, routes. Your own page adds her memory of you |
| `/plans`, `/plans/<id>` | plans by week and by month; a plan's venue, who is coming, add to calendar, open in maps, share |
| `/documents`, `/documents/<id>` | what Nora has written up for the rooms you are in, newest first; one document drawn from its stored spec in the portal theme. Pictures come through `/api/v1/documents/<id>/media/<sha>` (raster only) |
| `/games`, `/games/<id>`, `/games/<id>/players/<key>` | servers; a server's overview, players, activity, insights and world; a player's character, body, combat and playtime |
| `/nora`, `/nora/memory`, `/nora/doctrine`, `/nora/abilities` | about her and her status; her memory of you; her doctrine as a reader; her tools and skills |
| `/login` | Discord sign-in, and the refusal when an account is not on the list |

On a phone the bottom bar holds Today, Map, Plans and Games; People is one
tap from Today and the map, and Nora is her own mark in the top bar
(DESIGN.md §7: four destinations). ⌘K or `/` opens a search over everything;
`g` then `t`, `m`, `p`, `l`, `g` or `n` jumps to a page. The first portal's
URLs (`/status`, `/memory`, `/doctrine`, `/tools`, `/events`, the old game
sub-pages) redirect to where their content lives now (`next.config.ts`).

## How it reaches Nora

The browser only ever calls this app. `src/app/api/v1/[...path]/route.ts`
checks the session against `identity.toml` (`src/server/session.ts`, the one
place that check lives), then forwards to Nora's `:9100` API only what the
table in `src/server/upstream.ts` allows: GET only, named query parameters
only, id-shaped path segments only. Memory and events are the viewer's own:
the proxy puts `personId` from the session on the upstream call and ignores
any the browser sends. `src/server/upstream.test.ts` pins every rule, and
`e2e/portal.spec.ts` checks the memory rule against the running app.

Every read is typed at the edge (`src/lib/api/*.ts`, zod schemas mirroring
`nora/web/*.py`) and ends in one of four states the pages render differently:
the answer, not found, couldn't check (network, 5xx, or a body this build
cannot read), or signed out. "Couldn't check" is never shown as "nothing
here".

## Offline, and the app

TanStack Query keeps every successful read on the device in IndexedDB, one
store per person, for a week (`src/components/providers/query-provider.tsx`);
a page opened offline shows what it last knew and says so. The service worker
(`public/sw.js`) caches pages network-first, static assets and map tiles, and
never `/api/*`. Signing out deletes both. Bump `VERSION` in `public/sw.js` when
its strategies change; a new version waits and the page offers a reload.
Icons: `uv run --no-project --with pillow python app/scripts/make-icons.py`.

## Design

`DESIGN.md` binds it, as it binds the site: `src/styles/globals.css` holds its
tokens one for one, in both lightings (Studio and Late night, stored under the
same `nora-theme` key the site uses, so the choice carries between them). The
map is MapLibre over OpenFreeMap's vector tiles in a style of its own
(`src/components/map/map-style.ts`). A person's colour is an OKLCH hue at one
lightness and chroma (`src/lib/person.ts`); it always travels with their
initials or name, because hue alone cannot separate seven people for every
reader. Charts follow one axis, 2px lines, denim for a single series.

zh-Hant is the default and is written Cantonese; dates and relative times are
formatted as Cantonese too (`src/lib/format.ts`, `intlLocale`). Copy lives in
`messages/{en,zh-Hant}.json`: the same keys and the same placeholders
(`src/i18n/messages.test.ts`), and the same claims
(`tests/architecture/test_portal_locales_make_the_same_claims.py`, which does
run in `make gate`).

## Run it

```bash
cd app
npm ci
cp .env.example .env.local   # then uncomment the "Local development" block
npm run dev:all              # mock API on :9199 and the portal on :3400
```

`dev/mock-nora.mjs` serves every read the portal makes from generated data
(invented people, invented places), moving in time: one person drives a loop,
one is stale, one has sharing off. `MOCK_LATENCY_MS`, `MOCK_FAIL=games,presence`
and `MOCK_EMPTY=events` exercise loading, unavailable and empty states. With
`NORA_DEV_LOGIN=1` the login page offers the people in
`dev/identity.dev.toml`; a production build has no such sign-in whatever the
environment says.

## Check it

```bash
npm run typecheck
npm test            # vitest: the proxy table, formatting, presence, plans, games, the mock against the schemas
npm run e2e         # Playwright + axe: every page on a phone and a desktop
npm run build       # the standalone server the image runs
```

None of these run in `make gate`, which is Python only; run them before a PR
that touches `app/`.

## Deploy

`docker-compose.yml`'s `app` service builds this directory
(`docker compose build app` on the deploy host, `--network=host`) into a
standalone Next.js server on `:3000`, published on `127.0.0.1:3400`. Its
environment is the block at the top of `.env.example`; `identity.toml` is
bind-mounted read-only. Nothing here is bind-mounted: a change needs a rebuild
and a recreate (the `nora-deploy` skill).
