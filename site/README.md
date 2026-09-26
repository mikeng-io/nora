# site — Nora's public site

The page at `/` (繁體中文) and `/en/` (English): what Nora is, shown rather than
listed. Static Astro, no client framework. Three small scripts do the work in
the browser: the chat demo (`site/src/scripts/chat.ts`), the lighting and
language choices (`site/src/scripts/prefs.ts`) and the character-sheet viewer
(inline in `site/src/components/Form.astro`). Without script the page still
reads in full: the chat renders as a finished transcript.

`/` is the zh-Hant page, as the portal's default is. An English-first browser
that has not picked a language is sent to `/en/` (the portal's next-intl did
the same); picking one in the switch is remembered.

It replaces the portal's old landing route (`app/src/app/[locale]/page.tsx`).
The portal is still the only place anyone signs in; every "Sign in" here links
to the portal's `/<locale>/login`.

## Design

`DESIGN.md` binds it: its tokens are `src/styles/tokens.css` one for one, and
its two themes are the page's two lightings, Studio and Late night. The hero
portrait and the character plate switch with the lighting, the studio portrait
by day and its late-night twin by night. What the page adds to DESIGN.md is a
display register (the large type a public page needs), recorded in DESIGN.md
§4.

Motion follows DESIGN.md §6: nothing moves until someone acts. Messages enter
in 240ms; her thinking dots are the one loop, and only while she "thinks";
changing the lighting is a 240ms cross-fade.

## Run it

```bash
cd site
npm ci
npm run dev       # http://localhost:4321
npm run build     # astro check, then the static build in dist/
```

The build fails if a page uses a Chinese character that the committed Noto
Sans HK subset does not have (`site/scripts/cjk-coverage.mjs`). After changing
Chinese copy, run `npm run fonts` (it downloads the 11 MB source font once
into `.cache/`) and commit `src/fonts/noto-sans-hk-subset.*`.

Icons and link-preview images are generated, not drawn:
`uv run --no-project --with pillow --with fonttools --with brotli python site/scripts/make_images.py`
from the repo root. The art is in `src/assets/art/` (provenance in its
`README.md`); Nora's canonical images come straight from the repo's `assets/`.

## Copy

`src/i18n/en.ts` and `src/i18n/zh-hant.ts`, checked against one shape
(`src/i18n/types.ts`) by `astro check`, so a key in one language and not the
other fails the build. Every capability the page claims is from `README.md`'s
opening and its tool list; change the page when those change.

## Going live

The container serves the build on `127.0.0.1:3401` (`docker-compose.yml`,
service `site`, profile `site`). It owns only these paths:

```text
^/$  ^/en/?$  ^/_astro/  ^/_site/  ^/favicon\.ico$  ^/robots\.txt$
^/sitemap\.xml$  ^/llms\.txt$
```

Nothing there collides with the portal (`/_next/`, `/api/`, `/zh-Hant/…`,
`/en/…`, `/manifest.json`, `/sw.js`), so the site can share the portal's
hostname: the tunnel sends those paths to `:3401` and everything else to the
portal, as it already does for `/i/` and `/drop/`. Owning `/robots.txt` means
answering for drop too: `src/pages/robots.txt.ts` keeps drop's "never index"
for `/drop/` and `/i/`. In order:

1. `docker compose --profile site build site && docker compose --profile site up -d site`
2. The tunnel's ingress: the paths above to `http://localhost:3401`, before the
   portal's catch-all.
3. The portal's `/[locale]` page stops being a landing page: it redirects to
   its own `/[locale]/login`, which forwards a signed-in person to `/status`,
   and the old landing copy is deleted. Not to the site: without the tunnel
   (local dev) `/` reaches the portal, which sends it back to `/zh-Hant`, a
   loop.

Live since 2026-09-26: the tunnel rule is in `~/workspace/cloudflared/terraform/tunnel.tf`
on norty, just before the portal's catch-all.

If the portal ever gets a hostname of its own, build with
`PUBLIC_PORTAL_ORIGIN=https://<portal host>` so the sign-in links follow it,
and `SITE_ORIGIN` for the canonical URLs.
