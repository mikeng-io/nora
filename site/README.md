# site — Nora's public site

The page at `/` (繁體中文) and `/en/` (English): one story told as a journey up
a painted mountain path, "memory remembers, knowledge understands, Nora acts"
(DESIGN.md §9). Static Astro, no client framework.

## How the page works

The story is written once, as HTML, in `src/components/Journey.astro`: eleven
scenes, each a still and its words. That is the whole page without script,
with reduced motion, on a data saver or without WebGL2, and what every reader
and crawler gets.

Otherwise `src/journey/main.ts` turns the same words into the stage
(DESIGN.md §6, the stage register):

| file | what it does |
|---|---|
| `stage.ts` | the renderer, the camera, the painted stations as depth plates, mist, and one grade (light of the hour, the rewind's tape, the soulbound split, vignette, grain) |
| `world.ts` | the eight stations and the story's motifs: slips, threads, lanterns, the constellation, the travelling slip |
| `timeline.ts` | which scene owns which span of the scroll, and the cloud and fog banks between stations |
| `director.ts` | one function per scene: camera, light, what Nora does |
| `nora.ts` | Nora's layered rig, played by the LivLiv player into its own canvas and read as a texture, lit by the scene |
| `type.ts` | each title's motion (rise, drop, link, ignite, crop), keeping the words real text |
| `ui.ts`, `chat.ts` | the hold that lights her lantern, labels that ride on painted things, the soulbound drag, the scene rail, and the act scene's chat with "How she knew" |

Every interaction has a scroll fallback; none is needed to read on.

## Her art

The world is paintings in one anime background medium, kept outside the
repository with their prompts. `scripts/make_journey.py <art-root>` writes them
as WebP under `src/assets/journey/<station>/`, composes the scene stills, and
writes `meta.json` (where Nora stands, the pavilions, the moon gate). Nora's
rig is cut from one master drawing and ten aligned edits by
`scripts/make_nora.py <edit-set>` into `src/assets/journey/nora.livliv`.
`assets/visual-assets.json` lists every image with its provenance.

## Run it

```bash
cd site
npm ci
npm run dev       # http://localhost:4321
npm run build     # astro check, then the static build in dist/
```

The build fails if a page uses a Chinese character that the committed Noto
Sans HK subset does not have (`site/scripts/cjk-coverage.mjs`). After changing
Chinese copy, run `npm run fonts` and commit `src/fonts/noto-sans-hk-subset.*`.
Icons and link-preview images: `site/scripts/make_images.py`.

## Copy

`src/i18n/en.ts` and `src/i18n/zh-hant.ts`, checked against one shape
(`src/i18n/types.ts`) by `astro check`. Every claim is a behaviour
`design/cognition/` defines; the chat is a scripted example and says so.

`/` is the zh-Hant page, as the portal's default is. An English-first browser
that has not picked a language is sent to `/en/`; picking one is remembered.

## Going live

The container serves the build on `127.0.0.1:3401` (`docker-compose.yml`,
service `site`, profile `site`). It owns only these paths:

```text
^/$  ^/en/?$  ^/_astro/  ^/_site/  ^/favicon\.ico$  ^/robots\.txt$
^/sitemap\.xml$  ^/llms\.txt$  ^/wiki(?:/.*)?$ (redirects to /)
```

Nothing there collides with the portal (`/_next/`, `/api/`, `/zh-Hant/…`,
`/en/…`, `/manifest.webmanifest`, `/sw.js`, `/offline.html`, `/icons/`,
`/vendor/`, `/screenshots/`; app/README.md), so the site can share the portal's
hostname: the tunnel sends those paths to `:3401` and everything else to the
portal. Owning `/robots.txt` means answering for the portal too:
`src/pages/robots.txt.ts` keeps "never index" for `/artifacts/`, `/api/` and the
signed-in locale pages (Drop's `/drop/` and `/i/` rules left with Drop, ADR 0023). In order:

1. `docker compose --profile site build site && docker compose --profile site up -d site`
2. The tunnel's ingress: the paths above to `http://localhost:3401`, before the
   portal's catch-all.
3. The portal's `/[locale]` page stops being a landing page: it redirects to
   its own `/[locale]/login`, which forwards a signed-in person to `/status`,
   and the old landing copy is deleted. Not to the site: without the tunnel
   (local dev) `/` reaches the portal, which sends it back to `/zh-Hant`, a
   loop.

Live since 2026-09-26: the tunnel rule is in `~/workspace/cloudflared/terraform/tunnel.tf`
on norty, just before the portal's catch-all. `/sitemap.xml` and `/llms.txt`
joined it on 2026-09-27; a path added to the list above is unreachable until
it is added there too.

Build with `SITE_ORIGIN=https://<host>` if the canonical URLs ever move. The
site links to nothing in the portal: it is an introduction, with no sign-in.

