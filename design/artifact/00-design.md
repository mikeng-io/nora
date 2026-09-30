---
type: design
title: "Documents: json-render specs, stored by Nora and opened in the portal"
description: "Early study. What Smith called an artifact becomes a document: a small JSON spec over a closed catalog of shadcn components (json-render), validated in Nora's container, stored in Postgres, listed and opened inside the signed-in web app with the portal's own theme. Retires Drop's artifact half. The sandbox side (files only, never a page; retiring make_artifact and Smith) belongs to design/sandbox-runtime."
tags: [nora, documents, artifact, json-render, shadcn, react, portal, drop, skill]
timestamp: '2026-09-30'
status: draft
---

# Documents

> **Amended 2026-10-01 (coordinator decision, sandbox session; Miles's ruling).** Publishing a document is
> **`document_publish(spec | path, title?, document_id?)`, a document tool of this session's**, not
> `computer_export(to="document")`. `computer_export` never had that option and never publishes a page: as of
> the sandbox session's #649 `html` is refused and `link` only carries files (CSV, PDF, PNG); design/sandbox-runtime
> §6 records it (#650). `path` is read through that session's audited workspace-read helper, never through
> `SandboxRuntime.fetch` directly (that would bypass `computer_events`, the room lease and the scope derivation),
> and the inline `spec` form is built first. The must-have rule lives in `document_publish`'s own description and
> in doctrine. This note supersedes every `computer_export` mention below that predates it; the older text is
> left, marked, where it records how the design got here.

Status: **early study; direction set by the operator 2026-09-30; open decisions at the end.**

## 1. Decisions already made (operator, 2026-09-30)

1. **An agent emits a JSON spec, never HTML, CSS or JS.** A fixed renderer turns it into a page.
2. **Built on React, shadcn and json-render** (`vercel-labs/json-render`).
3. **Documents open only behind the portal's sign-in** (`identity.toml`). There is no public link.
4. **They are listed and opened in the web app**, not on a separate surface.
5. **They reuse the portal's whole style and theme.**
6. **The word is "document"**, not "artifact".

Decisions 3 and 4 remove the reason Drop's artifact half exists (§2), so this design retires it.

## 2. Scope and the line with the sandbox session

This session owns **what a document is, how it is checked, stored and shown.** The sandbox
session (`design/sandbox-runtime/00-design.md` §6) owns **how bytes leave the VM** and
retiring `make_artifact`, Smith and `LocalDockerBackend`.

The one contract between them (amended 2026-10-01, see the note at the top):

> `document_publish(spec | path, title?, document_id?)` is this session's tool. Nora checks and
> stores the document and the result is `{status, document_id, url}` where `url` is a portal path, or
> structured findings on a refusal. `path` is a spec file the model generated in the computer's workspace,
> read through the sandbox session's audited helper. The computer produces files and numbers; it never
> publishes a page.

## 3. Why the present shape fails (read from the tree, 2026-09-30)

- **Smith writes HTML by hand.** `nora/compute/smith/skills/html_report/render.py` wraps
  fragments in `page_shell.py`; the doctrine spends paragraphs on HTML-assembly mistakes
  (a whole `<html>` document nested inside a `<div>`).
- **Style drifts per run.** Only `page_shell.py`'s CSS tokens are shared, and a model can
  step around them. They are also a *second* copy of a theme the portal already owns
  (`app/src/styles/globals.css`, which mirrors `DESIGN.md`).
- **Output tokens are the binding ceiling** (12,000 per tag turn, silent; sandbox design §6).
  Hand-written HTML and inline SVG spend it on markup.
- **Drop serves `text/*` inline** (`deploy/drop/rendering.py::is_inline`), so an HTML artifact
  runs as a live page with no CSP. Its isolation came only from being a separate origin.
- **Drop's artifact half does a job that is no longer needed.** It is a public viewer with bot
  hardening, its own image, a RustFS client, a Postgres client, a token API and a network
  membership, all there so Smith, an untrusted container, could publish. With Smith gone and
  access behind sign-in, Nora can write the row herself. Drop's **link shortener** is a
  different feature and stays.

## 4. Architecture

```text
Nora (skill: document-creation)
  │  writes the spec: inline, or as spec.json in the workspace (VM)   ← the model's only authored output
  ▼
document_publish(spec | path)                         ← this session's tool
  │  inline, or bytes out of the VM through the sandbox session's audited helper
  ▼
Nora container: document-check                        ← §7
  │  parse · schema · rules · sha256 computed here
  ▼
Postgres: documents (spec as jsonb)                   ← Nora writes it directly (§5)
  ▲
  │  GET /web/documents, /web/documents/<id>          ← Nora's :9100 API, read-only
  │
app/ (Next, signed in via identity.toml)
  │  proxy allowlist row → list + detail routes (§6)
  ▼
Browser: json-render <Renderer> + shadcn registry, themed by globals.css (§8)
```

One package of knowledge, three consumers: **the catalog.** It is defined once (Zod, in
`app/`), and from it come (a) the renderer's registry, (b) an emitted `document.schema.json`
that Nora's Python checker reads, (c) the prompt fragment the skill embeds. The prompt, the
validator and the renderer cannot disagree because they share one source ("interpret once").
A test fails when the committed `document.schema.json` is stale against the catalog.

## 5. Storage

- **Table `documents`**, a new one through Nora's alembic chain (Nora owns the schema, as she
  does `artifacts` today):
  `id` (uuid), `title`, `spec` (jsonb), `spec_version`, `sha256`, `room`, `created_by`,
  `turn` (provenance), `created_at`, `updated_at`, `revision` (int).
- **Revision keeps the id.** Updating replaces the spec and bumps `revision`; the link never
  changes. Keep prior revisions? Open decision 4.
- **Specs are small** (KB), so they live in Postgres. RustFS is not involved unless a document
  embeds an image (open decision 5).
- **Nora is the only writer.** The web app is read-only, matching the proxy's GET-only table.
- **Retired:** `artifacts`, Drop's `POST /api/artifacts`, `/api/tokens`, `DropArtifactIngest`,
  the `nora-compute` membership, and the `ArtifactIngest` port for this use. The 4 real HTML
  artifacts (and 11 other stored files) are open decision 6.

## 6. In the web app

- **Routes:** `/documents` (list) and `/documents/<id>` (one), inside the `(portal)` group,
  so sign-in, the shell, the search palette, the service worker and TanStack Query persistence
  all apply for free.
- **Reads:** two rows in `app/src/server/upstream.ts`:
  `["documents"]` and `["documents", ":id"]` with an id-shape check (UUID).
  Typed at the edge like every other read (zod in `app/src/lib/api/`, mirroring a new
  a new documents module beside the other `nora/web/` ones), ending in the same four states (answer / not found / couldn't check
  / signed out).
- **Offline:** the query cache already keeps a successful read for a week per person, so a
  document opened once opens offline. No new work.
- **Where it lives in the nav:** DESIGN.md §7 fixes four bottom-bar destinations, so documents
  are **not** a fifth. Proposed: reachable from Today, the ⌘K palette and Nora's page. Needs a
  DESIGN.md decision (open decision 3).
- **Listing shows** title, when, by which room and who asked, and revision. Search over titles
  joins the palette.

### Visibility is unresolved, and is the one decision that can leak

The proxy has `scope: "self"` (the viewer's own). A document is made in a *room*, and rooms have
different members. **If any signed-in person can list every document, a document from a room
they are not in becomes readable.** Open decision 1 must be settled before S3.

## 7. The check pipeline ("lint")

Fail-closed, in order, each a distinct result. PASS needs all; ERROR is never PASS.

1. **Parse.** Valid JSON, size cap, depth cap, element-count cap.
2. **Schema.** Every element's `type` is in the catalog and its props validate; every child
   reference resolves; `root` exists; no cycles or orphans.
3. **Rules**, with their negative cases:
   - no component outside the catalog (including case-only variants);
   - **no link or image URL outside an allowlist, and no `javascript:`/`data:` scheme**;
   - `Markdown` content carries no raw HTML after parse;
   - text length caps so one prop cannot carry a page of markup;
   - required title on the document, required alt on `Image`.
4. **Serialize.** sha256 on the validated bytes; the row carries it.

**The threat model changes, and gets stricter.** Drop isolated artifacts on their own origin.
Documents render on the *portal's* origin, next to the session cookie. A component that let a
model emit raw HTML or an arbitrary `href` would be **stored XSS against a signed-in person.**
The closed catalog and these rules are therefore security boundaries now, not style rules, and
the viewer should ship a CSP for the document route.

Each rule gets a fixture that must fail, and a test that goes red when the rule is removed
(`nora-prove-fix`). Rendering quality (an empty chart, an overflowing table) is a separate
check: a headless render of fixture specs in the app's Playwright suite.

## 8. Reusing the portal's theme and adopting shadcn

**Verified 2026-09-30 with `npm view`:** `@json-render/core|react|shadcn` are `0.21.0`, Apache-2.0,
and the peers match the app exactly: `zod ^4`, `react ^19`, `tailwindcss ^4`
(app: zod 4.1, React 19.2, Tailwind 4.3). Pre-1.0, so pin an exact version.

**The app is not shadcn today.** `app/src/components/ui/` is hand-written on Radix, there is
no `components.json`, and `globals.css` runs `--color-*: initial` and defines its own names
(`studio`, `surface`, `hoodie`, `ink`, `denim`, `blush`…). shadcn components use semantic
classes (`bg-background`, `text-foreground`, `border-border`, `text-muted-foreground`,
`bg-primary`, `rounded-lg`…), which **do not exist in this theme**.

So "reuse the theme" is one alias layer, not a restyle. In `@theme inline`, map the shadcn
semantic names onto the existing tokens, for example:

| shadcn semantic | portal token |
|---|---|
| `background` | `--studio` |
| `card`, `popover` | `--surface` |
| `muted`, `secondary` | `--hoodie` |
| `foreground` | `--ink` |
| `muted-foreground` | `--ink-muted` |
| `border`, `input` | `--hairline` |
| `primary` | `--denim` |
| `accent` | `--blush-wash` |
| `destructive` | `--danger` |
| `ring` | `--denim` |
| `radius` scale | `--radius-control`, `--radius-panel` |

Both lightings (Studio and Late night) then follow for free, since the tokens already swap on
`data-theme`. The exact class list the package emits is read in S0, not assumed. Tailwind
must also scan the package's output (`@source`).

**Costs to accept, knowingly:**

- **Two component sets in one app.** The portal's own `ui/` stays; only the document surface
  uses the shadcn registry. Migrating the portal to shadcn is out of scope.
- **Duplicate dependencies:** the package depends on `radix-ui` (umbrella) while the app uses
  individual `@radix-ui/*`, and on `lucide-react ^0.564` while the app has `^1.48`.
  Expect a second copy of each in the bundle; measure it in S0.
- **An exposed component is a public surface.** Take only the ones a document needs, not all 36.

## 9. Catalog, first cut

A closed vocabulary; fix it against what was actually stored (15 rows, of which 4 are real HTML documents, 2026-08-12 →
09-04) before committing (S1).

| Need | Component |
|---|---|
| page, sections, layout | `Page`, `Section`, `Stack`, `Grid` |
| prose, code, tables | `Markdown` (no raw HTML), `Code`, `Table` |
| callouts, steps, grouping | `Card`, `Callout`, `Timeline`, `Tabs`, `Accordion` |
| numbers | `Stat`, `Badge` |
| charts | `Chart` (typed kinds: bar, line, area, pie, scatter) |
| diagrams | `Mermaid` (source string, rendered client-side) |
| locations | `Map` (see open decision 7) |
| media | `Image` (URL allowlist) |

- **Nothing takes raw HTML, a style string, a script or an arbitrary URL.**
- **Props are the smallest that express the need.** A `Chart` takes `kind`, `data`, `x`, `y`;
  colour comes from tokens. The portal already has a chart rule ("one axis, 2px lines, denim for
  a single series", `app/README.md`), which the `Chart` component should follow.
- **Actions and state are off.** A document is a document; interactivity is a later decision.
- **Smith's 49-chart gallery is not carried over as templates.** Its selection knowledge (data
  shape → chart kind) becomes a short table in the skill.

## 10. The `document-creation` skill

Replaces Smith's doctrine and six skills for this case. Short, because the catalog constrains:

- when a file earns its existence (kept from Smith's doctrine);
- the data-shape → `Chart.kind` table;
- "write `spec.json`; do not write HTML";
- the catalog prompt fragment, **generated from the catalog**;
- publish with `document_publish`, read the findings if the check refuses it, fix the spec and publish
  again (there is no checker inside the VM; see §23).

The must-have rule lives in `document_publish`'s own description and in doctrine, not only in the
skill, since `image-generation` was skipped 3 times in 6 live turns (amended 2026-10-01: it used to
name `computer_export`'s description).
Per `context-framing-beats-capability-listing`, the skill first says **what the model is
producing and what reads it** (a spec rendered by a fixed viewer inside the portal), then
lists components.

## 11. Sequence

| Step | What | Done when |
|---|---|---|
| **S0 spike** | In `app/`, install the three packages pinned; render 3 fixture specs on a scratch route with the alias layer; list the classes the package emits | fixtures render in both lightings; the unverified items in §8 are recorded here; bundle delta measured |
| **S1** | Read the 4 real HTML artifacts (done, §17); fix the catalog | list signed off |
| **S2** | Catalog module → registry, emitted `document.schema.json`, prompt fragment; drift test | schema regenerated from the same source the registry imports |
| **S3** | `documents` table, the Nora web read module, proxy rows, list + detail routes | **after decision 1**; a person outside the room gets 404, proven by a test that goes red when the rule is removed |
| **S4** | Python `document-check` with §7 rules and negative fixtures | each rule has a red-when-removed test |
| **S5** | `document-creation` skill against the catalog | eval: a fixed brief set yields specs that pass the checker, n ≥ 10 per brief |
| **S6** | Retire Drop's artifact half (decision 6 first) | compose and tests read back clean |

S4 needs nothing from the sandbox session; the `path` form of `document_publish` waits on its audited workspace-read helper (amended 2026-10-01: this used to wait on `computer_export`).

## 12. Open decisions

1. **Who can see a document.** Checked 2026-09-30: `identity.toml` maps persons to *accounts*
   only. Room membership lives in `policy.toml`, `[[rooms]] persons = [...]`, read at startup,
   and a room may say `persons = "*"` (anyone in that chat, including people who are not in
   `identity.toml`). `nora/web/memory.py` already scopes a read by viewer through
   `workspaces_of_person`; documents need the room-level analogue. Recommend:
   a document is visible to **its requester and to the persons its room names**, computed at
   read time from the loaded policy (so removing someone revokes access once the policy reloads, at the next
   restart; not a stored snapshot);
   for a `"*"` room, **the requester only** (fail closed, because membership there cannot be
   enumerated). Everyone else gets 404, the same answer the proxy gives for a bad id.
2. **Spec authority.** json-render's native spec, or a Nora envelope
   (`{version, title, spec}`)? Recommend the envelope: a version field is cheap now and
   impossible to add later. The `spec_version` column assumes it.
3. **Nav placement** and the DESIGN.md change that goes with it (§6).
4. **Keep prior revisions?** Cheap to store (KB); "make the chart blue" then has an undo.
5. **Images inside a document.** Blob store reference, or none at first?
6. **The 4 existing HTML artifacts** (11 other stored files are tests or uploads). They were public and unauthenticated. Options: let them
   expire, keep Drop read-only for them, or re-render. Recommend keep Drop read-only until they
   expire, then retire the artifact half.
7. **Maps.** Static image from the server-side Static Maps API (key stays on Nora's side), or a
   client map in the viewer (the portal already ships MapLibre). Recommend reusing MapLibre.
8. **Which session is the sandbox session**, so the §2 contract change can be sent to it.

## 13. S0 results (2026-09-30)

Spike: `@json-render/{core,react,shadcn}@0.21.0` pinned in `app/`, the §8 alias layer appended to
`globals.css`, three fixture specs rendered on a scratch portal route in Studio and Late night
(Playwright screenshots, no console or page errors). `npm run typecheck` and the 109 unit tests
still pass. Scratch files: `app/src/components/documents/spike.tsx` and
`app/src/app/[locale]/(portal)/documents/spike/` (delete before S3).

**Confirmed**

- The registry renders on the portal theme and follows both lightings through the alias layer
  alone. Emitted semantic classes are exactly: `background`, `foreground`, `card`, `popover`,
  `primary`, `secondary`, `muted`, `accent` (each with `-foreground`), `destructive`, `border`,
  `input`, `ring`. The table in §8 covers all of them.
- Tailwind needed `@source` on the package's `dist`; without it node_modules is not scanned.

**Found, and each changes the design**

1. **`className` is model-controlled.** `Card`, `Stack` and `Grid` take a `className` prop and
   pass it into the DOM (`cn(..., props.className)`). A spec could emit `fixed inset-0` and cover
   the portal. Our catalog **omits `className` from every component** and the checker rejects it.
2. **Spec fields beyond components.** An element may carry `on`, `watch`, `repeat`, `visible`, and
   the spec a `state`. With actions off (§9) the checker **whitelists the element keys** `type`,
   `props`, `children`, `slots` and rejects the rest.
3. **`Image` renders a real `<img src>` from any string.** A document could make a viewer's
   browser call an arbitrary host (tracking, and the IP and referrer leak). The URL allowlist in
   §7 is required, not optional.
4. **The package has no `Chart`, `Mermaid`, `Map`, `Markdown`, `Code`, `Stat`, `Timeline` or
   `Callout`.** Its 36 components are layout, content, navigation, overlay, feedback and input.
   §9's rows for those are **custom components we write** and register beside the standard ones.
   `Alert` covers the callout; `Text` has a `code` variant.
5. **`Alert` loses its colours.** Its info, success and warning types hard-code `green-*`,
   `yellow-*` and `blue-*` classes, which `--color-*: initial` removes, so all three render
   neutral (only `error` is coloured). The dialog overlay uses `bg-black/50`, likewise undefined.
   Replace `Alert` with a custom one on `success`, `caution`, `denim` and `danger`.
6. **`Stack` defaults to `align: start`**, so children shrink-wrap: cards, grids and tables
   rendered at content width. Our registry wrapper defaults `align` to `stretch`.
7. **`Tabs` does not switch content.** It renders its children as-is; showing one panel at a time
   needs state plus `visible`, which we turn off. A custom `Tabs` built on `slots` (one slot per
   tab value) is required, or tabs leave the first catalog.

**Not measured:** bundle size delta from the duplicate `radix-ui` and `lucide-react` copies, and
streaming (`createSpecStreamCompiler`). Both can wait for S2.

**Net for §9 (revised after seeing the first render, which looked wrong):** the package's raw
layout components break the portal's own rule (DESIGN.md Hairline-First: a panel is one hairline,
never a card inside a card), so they are not used. Take only `Table`, `Badge`, `Progress`,
`Separator` and `Accordion` from the package, and write the rest on the portal's primitives
(`Panel`, its type scale and tokens): `Stack`, `Grid`, `Card`, `Heading`, `Text`, `Alert`,
`Tabs`, then `Image`, `Chart`, `Mermaid`, `Map`, `Markdown`, `Stat`. None takes `className`.
The second spike render (desktop and phone, both lightings) shows full-width panels, coloured
alert bars on `denim`, `success`, `caution` and `danger`, a working slot-based `Tabs`, and a
single-column grid on the phone. So shadcn supplies the interactive and table primitives, and
the portal supplies the look; "adopt shadcn" means this split, not a wholesale swap.

## 14. Search, maps, templates, scenarios (2026-09-30)

**Search (web app ⌘K and the agent), what exists.** The `embedding` table (one table, open
`kind`, `row_key`, `scope`, `v1024` for bge-m3, HNSW cosine, `fingerprint` as the re-embed
predicate) and a `pg_trgm` lexical channel are in the schema. **Nothing writes or reads the
embedding table any more:** its only consumer was the knowledge tier, deprecated 2026-09-07 with
its code deleted (`design/dreamer-agent-loop/24-embedding-seam.md`, issue #115); the schema
stays. The lexical channel was measured on Cantonese and English (`word_similarity('大步行',
'我今日去咗大步行')` = 0.5); a `tsvector` cannot segment Chinese at all.

- **Serialize once.** The checker (§7) already walks every element, so it also emits a
  `documents.text` column: title plus every human-readable prop, joined. One extraction, read by
  both the portal and the agent. Never re-derived per reader.
- **v1 is lexical:** a trigram GIN index on `text`, filtered by the §12.1 visibility rule
  *before* ranking, exposed as one read row for the palette and one agent tool. No embedder to
  rebuild.
- **v2 is semantic, later:** `kind="document"`, `scope=room`, reusing the dormant table.
  Rebuilding the `Embedder` port is real work, so it is gated on a measurement: real queries
  against real documents where trigrams miss. Not built on a guess.

**Map.** A custom `Map` component over the portal's own MapLibre (`app/src/components/map/`),
taking markers and an optional route as coordinates. The spec carries coordinates, never an
address: geocoding is a tool step before writing the spec (Smith's `geocode` skill is the
precedent). No Static Maps key, no extra origin, no CSP change.

**Templates.** Not a runtime template engine. A template is an **example spec the skill shows**
(few-shot), one per scenario below, so style stops drifting without a second mechanism. Smith's
"template-first" chart rule is the precedent. They live next to the catalog and are validated by
the same checker in CI, so an example cannot rot against the schema.

**Scenarios (hypotheses; the real corpus is only 4 documents, §17).** Report with stats and a table;
chart-led summary; itinerary or plan with a map; comparison table; research brief with sources;
diagram with prose. Each becomes one template and one S5 eval brief.

**Presentation.** Open: slides, or only page layout? Smith's six skills never produced slides, so
a `Deck` is out of the first catalog unless S1 shows a real need.

**Container, layout, Tailwind.** The portal shell owns page width, padding and chrome, so a
document has no outer container of its own. Layout is `Stack` (one direction) and `Grid` (fixed
column sets that collapse to one on a phone), and sectioning is `Card` on `Panel`. Tailwind is an
implementation detail of the renderer: a spec never contains a class, only enum props.

## 15. Format and orchestration, from the December-trip scenario (2026-09-30)

**Scenario.** "We are going on a trip in December. Research it and make a document to prepare and
to present."

**Format.**

- **Stored and rendered form: the JSON spec.** It is the only form that can be checked against a
  closed vocabulary, rendered without trusting the author, and mined for search text. **XML is
  out:** nothing in this stack reads it, and the operator already met its problems in Smith.
- **Prose is Markdown inside the spec.** A trip is mostly prose, lists and tables, so a `Markdown`
  element (raw HTML disabled) carries the paragraphs; the spec carries the structure around them
  (sections, a day-by-day timeline, a map, a cost table).
- **Authoring form is undecided, and decided by measurement.** Writing long Markdown inside JSON
  strings risks escaping errors (newlines, quotes). Two candidates share the *same* stored form:
  (a) the agent writes the spec directly, nested rather than flat (`@json-render/core` exports
  `nestedToFlat`; verify in S2), or (b) the agent writes Markdown with a few fixed directive
  blocks (map, table, chart, timeline) that the checker compiles to the spec. Start with (a),
  since (b) means building a compiler. W7's eval runs the trip brief n ≥ 10 times and reads the
  valid-spec rate; (b) is built only if (a) fails there. Nothing downstream changes either way.

**Orchestration.**

1. The request arrives in a room. Nora gathers facts with her existing tools (search, places,
   weather, transit, geocode). This is research, not authoring.
2. The facts are fixed once, as verified input with their source and fetched-at time. Authoring
   and every later revision compute on those facts and never re-research (CMS: interpret once).
3. She writes `spec.json` in the workspace: nested sections, prose as Markdown, coordinates for the
   map, a `Sources` block listing each URL with its as-of date.
4. `document_publish` runs the checker, extracts the search text and stores it (amended 2026-10-01).
5. She replies in the room with the portal link. A revision edits the same spec and keeps the id.

**Where it breaks today, and what that decides.** A December trip is heavy research: it will not
reliably fit one tag turn (360 s, 20 model turns, 12,000 output tokens, which binds silently). So
the first version handles small trips inline, and the real home for this scenario is
`delegate_task`: research and authoring in the background, deliverable a `document_id`. That is
the reason the contract between the two designs is fixed now (§2), even though `delegate_task` is
built later.

**Presentation is a view of the same document, not a second format.** The portal gets a
`?view=present` mode: each top-level `Section` becomes a slide, with full-screen and keyboard
navigation. So no `Deck` component and no slide format. The cost goes into the skill: one idea per
top-level section. This closes open decision 8 (slides).

**Catalog additions the scenario forces:** `Timeline` (day by day), `Sources` (URL, title, as-of
date; provenance is a first-class element, not a footnote), and a static `Checklist`
(preparation). **Deferred:** a tick-able checklist needs state and a write path; the portal is
read-only and actions are off, so it waits. **Also deferred:** photos (`Image`, decision 5), which
trips will want and which need the blob store plus an allowlist.

## 16. Media and diagrams (2026-09-30)

**Mermaid.** A custom `Mermaid` component whose prop is the diagram *source string*, rendered in
the browser, lazy-loaded only on a document that contains one, at mermaid's strict security level,
themed from the portal tokens so it follows both lightings. The source is plain text, so it is
also searchable (§14). Smith pre-rendered static SVG; that ties the diagram to a render step and
loses dark mode, so it is not carried over. Checked in the spike, not assumed: bundle size, dark
theming, and that strict mode blocks click handlers and HTML labels.

**Images: reference custody, never a URL.** The store to reuse is the **blob store plus the unified
media index** (`nora/domain/media.py` `MediaObject`, DEC-138): bytes keyed by their sha256 in the
same RustFS every acquisition path uses, with `source` (inbound, generated, fetched) and
`provenance`. **Not** the `media_fetch` tool: that one *reads* an image and says what it shows; it
does not hand out bytes.

- **Authoring:** `Image` carries `media`, the short code Nora already sees in an `[image …]`
  marker. The checker runs in Nora's container, resolves the code through the media index, and
  **stores the sha256**, never the code. An unknown code is a rejection. This is "interpret once":
  the canonical spec holds the content address.
- **Free text:** `MediaObject.title`, `description` and `content` (the text in the picture) are
  already produced by the comprehension pass, so alt text and image text for search cost nothing
  and are not written by the model.
- **Generated illustrations** (the `image-generation` skill) land in the same index, so Nora's own
  pictures are usable in a document.
- **Bytes to the browser:** `GET /web/documents/<id>/media/<sha>`, served only when the viewer may
  see that document and the document references that sha. There is no general media endpoint.
  Raster types only (png, jpeg, webp, gif), **no SVG** (it can carry script), `nosniff`, and a
  CSP `sandbox` on the response. The portal proxy today forwards JSON; binary pass-through needs
  checking in W4.
- **The leak to close:** a document in room B must not publish a photo from room A. A media
  reference is accepted only if the object's recorded `platform` and `conversation_id` match the
  document's room, and unknown provenance is rejected (fail closed). Whether every `source`
  records those two fields is unverified and is a W4 check.

**External images are out of v1.** `media_fetch(url=…)` describes a URL's media on the fly and
**never stores it** (`nora/agent/tools.py`), so no sha exists to reference, and hotlinking is the
tracking hole found in S0. Allowing web photos needs a separate fetch-into-custody tool with its
own egress and SSRF screening; it gets its own design. This replaces open decision 5's default:
room media by reference, yes; external images, no. A trip document can use a hotel photo someone
posted in the room, not one from a website.

**Retention constraint, recorded for both designs.** Design 18 §8 leaves blob retention open and
no sweep exists today. If one is built, its live set is the hashes named by surviving message rows,
so it **must also count documents' media references**, or a document's picture disappears. The
viewer also renders a missing blob as "unavailable", never a broken image.

**Video.** Same custody, but browsers need range requests and the files are large, so it is not in
v1. A document shows a poster image with the stored description. Audio is out.

## 17. W1 / S1 results: what was actually stored (read 2026-09-30, norty, read-only)

**The "15 artifacts" figure is 15 rows, not 15 documents.** `artifacts` holds 15 rows
(2026-08-12 → 09-04): 8 are test fixtures from the day Drop went in (a 68-byte PNG, a 23-byte
PDF, a 17-byte zip, tiny text files, one already expired), 1 is a generated image, 1 a markdown
objective, 1 a gzip design bundle, and **4 are real HTML documents**, all between 08-14 and 09-04,
none since. The sandbox design §6 quotes "15 artifacts have ever been made"; that count should say
4 documents. **n = 4 is too small to size a catalog**, so the catalog is driven by the trip
scenario (§15) and these four are a sanity check, not a specification.

Structure only was read; nothing from the documents is quoted here (room content, scrub rules).

| Document kind | Text | What it is made of |
|---|---|---|
| a plan for an evening out | ~700 chars | headings, nested lists, bold, rules, one card |
| an update report | ~430 chars | headings, one list with bold and emphasis, one card |
| a nutrition quick-look | ~335 chars | a hand-drawn SVG bar chart, three-swatch legend, one note |
| a design visualisation | ~4,700 chars | six numbered sections, key-value rows, node-and-arrow diagrams, pills, one table, inline code |

**What this shows**

- **Zero `<script>`, zero external hosts, zero `<img>` in all four.** Nothing used interactivity or
  hotlinking, which supports actions off (§9) and no external images (§16).
- **Two of the four are pure prose and lists.** `Markdown` alone covers them, so most documents
  should be mostly one `Markdown` element inside a `Card`, not a deep component tree.
- **The chart is hand-drawn SVG**, the exact thing a typed `Chart` (bar, with a legend) replaces.
- **The big one maps onto:** numbered `Section`, key-value rows, `Table`, `Badge`, inline code
  (Markdown), and `Mermaid` for the node-and-arrow diagrams (likely; the SVG paths were not
  inspected).
- **Each document carries its own copy of the CSS** (1.1 to 5.8 KB), and two are byte-identical
  shells. That is the drift §3 describes, measured.

**Catalog v1, final list.** From the corpus: `Page`, `Section` (optionally numbered), `Card`,
`Markdown`, `Table`, `Badge`, `KeyValue`, `Chart` (bar first), `Mermaid`. From the trip scenario:
`Timeline`, `Sources`, `Checklist` (static), `Map`, `Image` (room media by reference). Everything
else in the package stays out.

## 18. W2a results (2026-09-30)

Built in `app/src/lib/documents/` and `app/src/components/documents/`; emitted files in
`nora/documents/`. Covers 12 components; `Chart`, `Mermaid`, `Map` and `Image` are W2b because each
needs a dependency or the media route.

**Decisions made while building, each from a measurement**

- **`catalog.prompt()` and `catalog.jsonSchema()` are not used.** The prompt is 17,048 characters
  and teaches `repeat`, `$state` and actions, all forbidden here. The schema checks the spec's shell
  only: `props` is `additionalProperties: {}`, so it never validates a prop. Ours are generated
  from the Zod props instead: a 14 KB JSON Schema (draft 2020-12, recursive) and a 4.4 KB prompt
  fragment (about 1,100 tokens).
- **The stored form is a nested tree,** `{version: 1, root: <Page>}`, not json-render's flat id map.
  A tree cannot have a dangling id, a cycle or an orphan, which removes three checks, and it spends
  no tokens on ids. The element keys are exactly `type`, `props`, `children`, so `on`, `watch`,
  `repeat`, `visible` and `state` cannot be written at all. `nestedToFlat` converts at render time.
  The envelope has no `title`; the Page's title is the document's title (serialize once).
- **`@json-render/shadcn` is removed.** None of the 12 components used it (the portal's
  primitives fit the theme, and two of its defaults were wrong there), so the dependency and the
  alias layer in `globals.css` are gone. `@json-render/core` and `/react` stay: catalog, registry
  and renderer are the part that earns its place. The package is a pre-1.0 option for a later
  component (Accordion, Tabs) and is easy to add back. This narrows "adopt shadcn" to "use
  json-render; shadcn components only where they fit"; the operator should confirm.
- **`className` and every unknown prop are rejected** by `z.strictObject` on each prop list.
- **Markdown** uses `react-markdown` (raw HTML is never parsed; images refused; links http(s) and
  mailto only, `rel="noopener noreferrer nofollow"`).

**Measured**

- 21 new unit tests (130 total) and 4 e2e (both lightings, phone and desktop, axe clean, no
  console errors). Sixteen forbidden shapes are each rejected.
- **Falsified by hand:** making `Section`'s props non-strict turned "rejects a className" red
  (1 failed, 20 passed); editing a description without re-emitting turned the `catalog-prompt.md`
  drift test red; after restoring (`cmp` byte-identical) 21 passed.
- **Limit:** the drift tests live in vitest, which `make gate` does not run (it is Python only). A
  stale emitted file is caught by `npm test`, not by the gate. W3's Python tests must therefore
  also validate a fixture corpus against the committed schema, so the checker is tested on its own.

**Open:** whether to accept the `@json-render/shadcn` removal (above).

## 19. More components, demos, and what replaces "park until a real need" (2026-09-30)

**Added to the catalog now (17 components):** `Stat`, `Accordion` (native `<details>`, body is
Markdown), `Code` (monospaced, no highlighting), `Tabs` with `Tab`. `Tab` is valid only directly
inside `Tabs` and `Tabs` holds nothing else, enforced in the schema, not by convention; the tab
labels live on each `Tab` and `toFlatSpec` derives the strip from them, so there is one source.
A `Table` column header may be empty (a blank corner cell). Five demo documents, invented content,
one per scenario (`trip`, `report`, `comparison`, `evening`, `runbook`), live in
`app/src/lib/documents/demos/`; a test fails if one stops validating or if the demos stop using
every catalog component. They are the viewer's fixtures and the examples the skill shows.

**Found by the e2e:** a wide table and a code block scroll sideways on a phone, and axe
(`scrollable-region-focusable`) flagged the unfocusable scroll region. Both now take focus and
carry an accessible name.

**No more "park".** The earlier rule, admit a component "on real need", had no owner and no
trigger. It is replaced by two concrete rules:

- **Admission:** a component goes in when a scenario brief in the eval (W7) cannot be written
  without it, or the demo that needs it has to fake it. The operator owns the scenario list; the
  eval measures whether the model then uses the component correctly.
- **Cost, which is the real gate:** each component is a permanent surface the model can use and the
  checker must guard. It needs a schema, a renderer, a demo, a negative fixture and an e2e. A
  component is not refused for lack of need, only for lack of those five things.

**Scheduled, each with its dependency and its step, not parked:**

| Component | Needs | Built in |
|---|---|---|
| `Chart`, `Mermaid`, `Map` | a chart library, mermaid, the existing MapLibre | W2b |
| `Image`, `Gallery` | the media route and the same-room rule (§16) | W2b, after W4's read module |
| `Person` (inline chip, name and the person's hue) | the persons list and the visibility rule | W5 |
| `Ref` (typed link to a plan, person, game or place by id) | the portal routes and the visibility rule | W5 |
| `Code` highlighting | a highlighter dependency | W2b, if the eval shows code-heavy briefs |

## 20. What the artifact skills changed (loaded 2026-09-30, after W2a was built)

The `artifact-design`, `artifact-diagramming` and `dataviz` skills were loaded late; W2a was built
first. Their page contract (CDN allowlist, sandbox frame) does not apply, since documents render
inside the portal, not as a claude.ai page. Their **principles** do, and three changed something:

- **"Structure is information."** Numbered markers belong only where order is the point. The
  `Section.number` prop existed, and five demos numbered sections that were not a sequence. The
  demos now number only the runbook (real steps), and the prop's description, which is what the
  model reads, says so.
- **Diagrams: one figure, one claim** (`artifact-diagramming`). `Mermaid` (W2b) renders inside a
  `<figure>` and takes a **required `caption`** stating what the picture shows, used as the
  figcaption and the `aria-label`; arrows must carry labels, which the skill's prompt fragment
  tells the model. Colours come from the portal tokens so both lightings read.
- **Charts (`dataviz`) set W2b's `Chart` requirements:** one axis only, never a dual axis; a typed
  form chosen by the data's job (and a `Stat` when the answer is one number); one hue for a single
  series and a fixed-order categorical set for several, **validated with the dataviz skill's
  palette validator against both portal surfaces** (light and dark) before it ships; a
  legend whenever there are two or more series, with direct labels for at most four; a hover
  tooltip; thin marks; a **data-table view of every chart** for accessibility; and the colour never
  carries identity alone. The portal's own rule (2 px lines, denim for one series) already agrees.
- **Copy:** no em-dash asides or "not X but Y" in demo text or in the skill's prompt fragment.

Checked against these and unchanged: the tokens and both lightings, consistent repeated elements
(`Stat` tiles, `Timeline` rows), cards used by role (a `Card` only around a group, never a `Card`
inside a `Card`), and content complete at rest (nothing animates in; the only motion is the
accordion chevron, 160 ms, `transform` only, off under reduced motion).

## 21. W2b results: Chart, Mermaid, Map, Image (2026-09-30)

The catalog is now 21 components. The prompt fragment is 6.8 KB (about 1,700 tokens) and the schema
26 KB. `Gallery` is dropped: a `Grid` of `Image`s is one.

**Reused, not rebuilt.** `Chart` draws with the portal's own `BarList` and `TimeChart` (one axis,
2 px line, denim for one series, hover and arrow-key crosshair). `Map` wraps `NoraMap`, the
portal's one map component, through `places`, `lines` and `fitTo`; a document with places in
Lisbon frames on Lisbon. `Mermaid` adds one dependency (`mermaid` 12.0.0), loaded only when a
document contains one.

**Rules now in the schema, each with a failing fixture:** a `Chart` has 2 to 60 points (one number
is a `Stat`), a `caption`, and only `bar` or `line`; there is no second axis and no pie. A
`Mermaid` has a `caption` and no `%%{` directive anywhere in its source. A `Map` has coordinates
in range and never an address. An `Image` names `media` as a hex code, never a URL, and requires
`alt`. The renderer shows a picture's description when the document has no stored id (fixtures),
and "no longer available" if the bytes are gone; it never issues a request it knows will fail.

**Defects found by looking, each now guarded**

- **The map rendered blank** while every check passed. `NoraMap`'s root takes its height from its
  `className`; with none, MapLibre drew its default 300 px canvas into a zero-height container, so
  the canvas and all four pins existed and nothing showed. The first e2e (text of the place list)
  and a second (canvas height, pin count) both passed on the broken map. The test now reads the
  map container's height, and was **proven red by removing the fix** (received 0, expected above
  250) and green again after restoring.
- **Mermaid 12 ignored `flowchart.htmlLabels`.** The key moved to the top level, so the diagram
  still emitted `foreignObject` HTML labels, contradicting the code's own comment. Set at the top
  level, the e2e now asserts no `foreignObject`, `script` or `onclick` in the output.
- **Hostile Mermaid source fails in the open.** Directives, an `<img onerror>` label and `click`
  handlers make mermaid refuse to draw; the block then shows "could not be drawn" with the source.
  The e2e listens for dialogs and asserts none fire, no `javascript:` link, no handler, no image.
- **Diagram text and edge labels** were tiny and struck through by the edge line; edge-label
  background is set to the surface token and the demo's labels are shorter.

**Known limits, not hidden**

- `TimeChart` spreads 3 to 5 x ticks evenly rather than labelling each point, so a 7-day line shows
  Mon, Wed, Thu, Sat, Sun. The full labels are in the chart's data table ("Show the data") and the
  hover. Labelling every category needs a change to `TimeChart`, which the portal's other charts
  share, so it is left for its own change.
- A `Mermaid` that fails to parse is only detectable in the browser, so the Python checker (W3) can
  catch the `%%{` rule but not a syntax error. The fallback is the defence.
- `Image` cannot show real bytes until the media route exists (W4). Its renderer and schema are
  done; the route is the open part.
- Charts with more than one series are not offered: the dataviz rules then require a validated
  categorical palette and a legend, which is its own piece of work.

## 22. Review of PR #644 and what it changed (2026-09-30)

An independent reviewer (a fresh Opus subagent, told to try to break the schema and to check the
claims) found four things confirmed by running them and two from reading. Each was reproduced
here before it was fixed, and each fix has a test that failed first.

| # | Finding | Fix | Proven by |
|---|---|---|---|
| 1 | **Mermaid 12 takes configuration from frontmatter and requests from `click`, `style` and `classDef`.** A source that passed the `%%{` rule could turn HTML labels back on, put `url(https://…)` in the SVG's `<style>` and add an `<a href>` to any site. Worse than the reviewer saw: mermaid draws into the live page to measure, so the request fires **before** any check of its output. A post-render filter alone still let `i.png` and the font url through (measured). | The schema and the renderer both refuse a source with frontmatter, `%%{`, `click`/`link`/`callback`/`call`, `style`/`classDef`/`linkStyle`, `url(`, `image-set(`, `@import`, `expression(` or `javascript:`, before mermaid sees it. A second wall checks the output is passive (no `foreignObject`, `a`, `image`, `on*`, external `href` or `url()`). | e2e with six hostile sources and a route on `evil.example`: red twice (5 active elements, then 4 outside requests), green after. |
| 2 | **The JSON Schema disagreed with Zod.** Defaulted props were *required* in the schema but *optional* in the prompt. Python's `$` matches before a trailing newline and `\d` matches other scripts' digits, so a link or date with a trailing newline, or Arabic-Indic digits, passed. | Emit with `io: "input"`; anchors are `(?![\s\S])` and `[0-9]`. | `tests/unit/test_document_schema.py` (in `make gate`): reverting the three anchors turns 7 tests red; restored emitted files are byte-identical. |
| 3 | **The Python checker's time grew exponentially with nesting.** `anyOf` makes `jsonschema` validate every failing branch in full: 18 s at depth 10, `RecursionError` deeper. | The schema is an `if type == X then …` chain, unrolled to the depth cap (8) instead of recursive. | Depths 9, 12, 60 and 400 are refused in under 2 s; a wide document at the cap validates in linear time. |
| 4 | **`safeParse` throws `RangeError`** on a 1,200-deep document. | `parseDocument` measures depth and size with an explicit stack first (caps: 8 levels, 500 nodes, 200 children) and never throws. | documents.test.ts. |
| 5 | **`documentId` went into an image path unchecked.** | Only a UUID is used, and the path is encoded. | e2e: `../../x?y=1` makes no request; a UUID asks for exactly its own media path. |
| 6 | **Tabs ignored the ARIA pattern.** | `aria-controls`/`aria-labelledby`, one tab stop, arrow keys, Home and End. | e2e. |

**Found while fixing, not by the reviewer.** The differential test (Zod against the JSON Schema on
one corpus) immediately caught my own rewrite letting no `Tabs` through: the generic `children`
rule also applied to a Tabs, so its `Tab` children failed. And an image that fails before React
attaches `onError` (server-rendered, then hydrated) never reports it and stays a broken icon; the
404 came back and nothing changed. Both are fixed and covered.

**Corrections to earlier text.** The prompt said "no URL except in `Sources`" while Markdown links
were accepted; a link costs a click, not a request, so Markdown links stay and the wording now says
so. The PR body said 16 rejected shapes; the corpus has 54 refused and 14 accepted documents. The fixture route, schema size (now 102 KB,
unrolled) and the prompt fragment (about 1,750 tokens) are restated in the PR.

**Still not covered.** No Content-Security-Policy on the portal, so the renderer and schema are
the only walls; a CSP for the document route belongs with the W5 routes. The Python checker does not
yet exist; this schema and corpus are what it will be built against.

### Second pass (same day): the first fix was incomplete

The reviewer was resumed and told to attack the fixes, not the original code. It ran its payloads
through `MermaidBlock`'s exact initialisation with `evil.example` routed and found the Mermaid channel
**still open**, plus a disagreement I had not seen and an over-rejection.

| # | Finding | Fix | Proven by |
|---|---|---|---|
| 7 | **A blocklist cannot name every whitespace Mermaid accepts.** Its frontmatter rule is `^([^\S\n\r]*)-{3}`, any leading space except a newline; mine allowed only space, tab and BOM, so ` ---` (also `\u000B`, `\u000C`, ` `, `　`) still changed the config and fired a request. | **An allowlist**: the source starts at index 0 with a known diagram keyword (no room for frontmatter), contains no control, format or non-ASCII space character, no lone CR and no backslash. | 12 hostile sources through `MermaidBlock` with the route on; removing the renderer's pre-check makes the e2e see `i.png`, `ff` and `n.png` (red), restored (green). |
| 8 | **A CSS escape hides `url(`:** `u\72 l(https://…)` passed both the source rule and `passiveSvg`. | No backslash at all; `style`, `classDef` and `linkStyle` lines may use only letters, digits and `# : ; , . % _ -`. | corpus cases in Zod, ajv and Python. |
| 9 | **`;` separates Mermaid statements and `links` is a sequence statement**, so `click` after a `;`, after a lone CR or after an NBSP indent, and `links A: {…}`, all drew an `<a href>`. Node metadata `A@{ img: "https://…" }` was an unlisted image channel. | Statement blocklist matches at a line start **or after `;`** and adds `links`, `href`, `properties`, `details`; `@{` is refused. | corpus; `passiveSvg` still caught the links as a second wall. |
| 10 | **Zod and Python still disagreed** on `\s` (U+FEFF passed in Python, U+001C in Zod), on `\b` (ASCII-only in JS, Unicode in Python) and on `1e400` (`inf` in Python). | No `\s` or `\b` anywhere; a link is printable ASCII (`[!-~]`); Chart values are bounded to ±1e15. | corpus; `test_a_non_finite_number_is_refused`. |
| 11 | **The 500-node cap cannot be stated in JSON Schema**; a 502-node document is refused by `parseDocument` and accepted by Python. | Not fixable in the schema. Pinned by `test_the_node_cap_is_a_rule_the_schema_cannot_state`: the W3 checker must count nodes. | Python test. |
| 12 | **The first fix over-rejected plausible documents**: `style Start fill:#f9f`, `classDef`, `linkStyle 0 stroke:#ff3`, a node named `call[...]`. | Those are accepted now (plain values only); a participant named `Call center` in a sequence diagram is still refused, by design. | 8 new valid corpus cases, including Cantonese labels. |
| 13 | `journey` diagrams always fail `passiveSvg` (Mermaid draws them with HTML) and showed the fallback. | `journey` is not an allowed diagram; the prompt says so. | corpus. |
| 14 | A tab panel had no `tabIndex`. | `tabIndex=0`. | e2e. |

Result: 22 accepted and 73 refused documents in the corpus, run through Zod, ajv and Python's
`jsonschema` with the same verdicts; 105 Python schema tests, 208 app unit tests, 44 e2e.

**What this says about the approach.** Two review passes found five channels that a model-written
Mermaid source could use, four of them after the first fix. The parser's grammar is larger than any
rule written against it, so the allowlist is a bound on what reaches Mermaid, not a proof. The second
wall (`passiveSvg`) and a Content-Security-Policy for the document route are the defence for what the
allowlist misses, and only the first exists today. If a third pass still finds a channel, the next
step is to stop drawing model-written Mermaid in the page (render it to an image in the sandbox)
rather than lengthen the list.

### Third pass: no channel found, two real defects, one conclusion

The reviewer was resumed a third time with one question: can any source the allowlist accepts still
make a third-party request, add an active element or change config when drawn? **57 attempts across
all ten allowed diagram types, drawn by mermaid 12.0.0 in Chromium with `evil.example` routed, found
none.** Start-rule near misses (`flowchart-elk`, `graph-foo`, `flowchartX`), `@` and `{` split by a
newline, HTML and entities in every label kind, `todayMarker` with `image()` and `cross-fade()`,
`rect rgb()`, `::icon()` and `:::` all came back inert. It did find two defects of its own:

| # | Finding | Fix | Proven by |
|---|---|---|---|
| 15 | **A failed draw leaves a visible error graphic in `<body>`**, outside the figure (six accepted sources did). | `suppressErrorRendering: true` (a secure key a source cannot override) and each draw removes its own scratch nodes. | e2e: four leftovers without the fix (red), none with it. |
| 16 | **A dense diagram froze the page for 3.4 s** (450 edges among 30 nodes, 3,612 characters). | `maxEdges: 100`, also a secure key. | e2e: both failing cases reach their fallback in about 320 ms and a round trip into the page answers in under a second. |

**One regression of my own, caught by the existing e2e.** Removing scratch nodes in the effect's
cleanup let React's dev double-invoke delete a node another draw was still using, and the diagram
never appeared. Each draw now has its own id and only cleans up after it settles.

**The reviewer's conclusion, recorded.** Drawing model-written Mermaid in the page is defensible
only conditionally. The weakness is structural: mermaid inserts its SVG into the live page before
any output check, so the only barrier is predicting its grammar from outside, and that prediction
failed twice. Rendering in the sandbox to a static SVG or PNG would remove the whole class, the
error-path leak and the freeze, at the cost of a render step per document and no live theme switch.
The decision here is to ship the allowlist with mermaid **pinned to an exact version** (a test
asserts it) and the hostile-source e2e and corpus as the re-run on every upgrade, and to **move to
sandbox rendering if a third bypass is found or before documents are visible to anyone outside the
rooms they came from.** Recorded as an open decision below.

**Open decision 9.** Render Mermaid in the sandbox instead of the page? Recommend not yet, for the
reasons above, and revisit at the W4 visibility work, when the audience becomes real.

## 23. No checker in the VM; Tabs on the portal's Radix tabs (2026-10-01)

**No VM-side checker.** §10 had the skill run a local check (`nora-artifact-check`) before export.
Dropped. The authoritative check has to run in Nora's container, because the VM is untrusted, so a
second copy inside the VM would be another implementation to keep in step with it, and two validators
reading one schema already cost three disagreements (§22). Instead `document_publish` (amended 2026-10-01:
this said `computer_export(to="document")`) runs the one check and returns **structured findings** on a
refusal (a path into the document, what was wrong, which rule). Nora fixes the spec, or the workspace file
with `computer_edit`, and publishes again. The skill says so.
The catalog's JSON Schema and the prompt fragment still ship to the model as text; only a runnable
copy of the checker is not shipped.

**Tabs reuse the portal's Radix tabs** (`app/src/components/ui/tabs.tsx`, `@radix-ui/react-tabs`,
already a dependency) instead of the hand-written ARIA and arrow-key handling the first version
carried. Radix supplies the roving focus, Home and End, `aria-controls` and `aria-labelledby`. A
consequence worth knowing: the inactive panel is not mounted, and the tab list is one keyboard stop
that lands on the selected tab (the first version made every tab a stop). The e2e now asserts that
behaviour instead of the `tabindex` attributes the hand-written version happened to set.

**Who builds what from here.** W3 (the checker, with structured findings) and W4 (the `documents`
table, visibility by room, the read API) are next and are this session's. Amended 2026-10-01: there is no `to="document"` change on the sandbox
side; `document_publish` is this session's tool, and the only thing crossing the boundary is the audited
workspace-read helper behind its `path` form.

## 24. The checker (W3) and its review (2026-10-01)

`nora/documents/check.py` judges every document in Nora's container: shape (size, UTF-8, JSON without
NaN, Infinity or a duplicate key, node, depth and width counts with an explicit stack, no control
character), the generated schema, then the rules the schema cannot state. It returns `ok`, `refused`
with structured findings, or `error`, never collapsed. On success it returns the title, the plain
search text, the canonical form and its sha256. The reviewer from PR #644 attacked it (PR #648); the
bytes path never said `ok` wrongly, but it found one CPU bug and several gaps, all reproduced first.

| # | Finding | Fix | Proven by |
|---|---|---|---|
| 1 | **Quadratic `_LINK` regex**: twelve Markdown nodes of 20,000 `[` (240 KB, under the cap) cost 9.5 s of CPU before being refused, because extraction ran before the text cap. | Linear patterns (a class that excludes its own delimiter); the text cap is checked as the text grows. | test under 2 s; 9 of the 10 new tests fail on the first version. |
| 2 | **A dict skipped every guard the bytes path has**: NaN passed (then a canonical form that is not JSON), a dict subclass lying to `.get` hid 1,000 nodes (`nodes=1`), a 100,000-deep list in props was `error`, no size cap. | Every input is written to JSON first and judged as bytes: one path. | tests for NaN, the lying subclass, deep props, size and a set. |
| 3 | **A lone surrogate was `error`**, so the model could not fix its file. | Refused with `unpaired_surrogate` and the path (or `encoding` for a Python string). | test. |
| 4 | **NUL and control characters reached `ok`** and would be refused by Postgres `text` and `jsonb` later. | Refused (`control_character`, with the path) in every string and key; tab, newline and CRLF stay legal. | test. |
| 5 | **One document had several hashes**: `2` and `2.0`, `-0.0` and `0`, an NFD and an NFC title. | Normalise before hashing: NFC on every string and key, integral floats as ints, `-0.0` as `0`; the title is returned NFC. | test. |
| 6 | **Findings read as schema-speak or echoed input**: a regex dump for an Image URL, the Mermaid source then a cut-off pattern, a Tabs mistake with the useful line last, an enum list cut mid-word, and up to 200 characters of a hostile key echoed back. | A sentence per rule in document terms, a plain reason for each pattern, a wrong `type` first, and content echoed back capped at 40 characters. | 10 realistic mistakes asserted, plus a prompt-injection-shaped key. |
| 7 | **A wheel would ship without the schema**, so every check would answer `error`. | `documents/*.json` and `documents/*.md` in the package data. | test against `pyproject.toml`. |
| 8 | **"Imports nothing of ours" was only a comment**: the layers contract stops a listed layer, and `nora.persons`, `nora.tag` and `nora.web` are outside the list. | A forbidden contract naming every other top-level package. | `lint-imports` reports the break when `check.py` imports `nora.persons`, and is clean after. |
| 9 | **Search text gaps**: Chart `unit` missing; bare URLs, autolinks and reference targets leaked in; Mermaid keywords and `classDef` colours leaked in. | Extraction updated for each. | tests. |

**Left as found, deliberately.** Python is stricter than the TypeScript `parseDocument` (a Card in a
Card, a short table row and a 2026-02-30 `asOf` are refused here and accepted there). That is safe only
while every stored document has passed this checker, which is the W4 requirement: nothing is stored
except through `check_document`. The node cap cannot be stated in JSON Schema and stays a rule here.

**One test attempt that proved nothing, recorded.** A mutation of the "error is never `ok`" rule did
not apply (`ruff format` had rewrapped the line), so its green run was not evidence; it was redone
against the real source and then turned a named test red.

### Second pass on the checker (same day): a regression the fix introduced

Re-run against the fixes, every earlier finding held, and the reviewer found that **the fix itself
had introduced a wrong `ok`** plus one pattern still slow.

| # | Finding | Fix | Proven by |
|---|---|---|---|
| 10 | **Normalisation ran after validation.** A Badge of U+0958 x 40 passed, and its stored NFC form was 80 characters; a Mermaid source with U+212A and U+037E passed, and NFC made it `clicK B "…"` and `;`. The checker's own canonical output then failed its own schema and the portal's. | Normalise **first** (iteratively, because the structure is not yet bounded), then judge the normalised form. Two keys equal after NFC are refused instead of one silently replacing the other. | the stored form is re-checked and passes with the same hash; the two inputs are refused. |
| 11 | **`_AUTOLINK` was still quadratic** (`<a:` repeated: 15.8x for a 4x input). | A class that excludes `<`. | a doubling test over 16 adversarial units and both extraction functions; it fails on the previous version at exactly `<a:`. |
| 12 | A dict whose `items()` raises was `error`, not a refusal. | Any failure while writing the input as JSON is a `json` refusal. | test. |
| 13 | **Bidi overrides (U+202A-202E, U+2066-2069)** passed: a title can display reversed. | Refused in every string and key. Zero-width characters, NEL, U+2028 and private-use stay legal (harmless here). | test. |
| 14 | **ESC in a Code block was refused**, though a runbook pastes terminal output. | Colour and cursor sequences are stripped from a Code block's `text` only; any other ESC, and ESC anywhere else, is still refused. | test. |
| 15 | The isolation contract named 20 of 22 packages (`nora.persons_file`, `nora.dreamer` missing). | Added, and a test builds the list from `ls nora` so a new package fails it. | the test failed first, naming both. |
| 16 | The unknown-keys message did not say how many. | "(and 9995 more)". | test. |

Not changed: a dict subclass that lies in `items()` is judged on what it wrote, so callers store
`Checked.canonical`, never the object they passed in (documented on `_to_bytes`).

### Third pass on the checker (same day): the same bug class, twice; now guarded as a class

The reviewer's third pass found that **`_strip_ansi` had the same flaw as the NFC ordering**: it ran
after normalisation and the schema, so a Code block that was only a colour code passed `minLength` as an
empty string (and failed its own re-check), and stripping joined characters that NFC had not seen side
by side, so one document had two hashes. Both regressions were a step that changes a string after it was
judged.

| # | Finding | Fix | Proven by |
|---|---|---|---|
| 17 | `_strip_ansi` after the schema and NFC. | Strip right after the character scan, before normalising and validating, on a tree nothing is assumed about yet. | a test on the emptied text and the joined jamo, red on the previous version. |
| 18 | The invariant was checked only case by case. | **A property test**: a seeded fuzz of 1,500 documents over characters that normalise, compose, grow or strip (combining marks, jamo, singletons, ESC sequences, bidi, NUL, CJK) through seven string-carrying components; whenever the verdict is `ok` the stored form re-checks `ok` with the same hash and canonical form, and nothing raises. | It fails on the version before the NFC fix (`bd3f8270`) and on the one before this fix (`6642e7a7`), each found by the fuzz and not by a hand-written case. Two first drafts of it were not good enough, and are recorded: it passed on `bd3f8270` because no string sat at a length limit (a boundary kind was added), and after that it passed on `6642e7a7` because adding a kind reshuffled one shared random stream and stopped generating the colour-code cases. Each kind now has its own generator and seed. |

The pipeline is now, in this order and for this reason: parse, shape, characters, strip terminal codes,
normalise, schema, rules, then text and the canonical form. Every step that changes a string runs
before the step that judges it, so what is stored is what was judged.

## 25. W4a: the store, the publish service and the read API (2026-10-01)

**Built** (PR for W4a): `nora/domain/documents.py` (the `DocumentStore` port, in its own module as
`nora.domain.compute` is, so it adds nothing to `ports.py`), migration `0084_documents`,
`nora/infra/sql/documents.py`, `FakeDocumentStore`, `nora/app/documents_service.py`
(`DocumentService.publish`, `rooms_named`), `nora/web/documents.py` with `GET /app/v1/documents` and
`/app/v1/documents/{id}`, and two rows in the portal's proxy table. No tool, portal page or skill yet.

**Storage.** `documents` (identity: id, room, creator; `recorded_at` is when it first existed) and
`document_revision` (one full snapshot per revision: title, the checked spec as jsonb, the search text,
sha256, the full sha256 of every picture it names, who wrote it). Both are **append-only**, so neither
changes in place and neither needs the row-history trigger; the current document is the highest
revision, which removes the head pointer that could drift. `(document_id, revision)` is the primary
key, so two revisions from one base resolve to one winner and one `conflict` (the contract suite races
six writers and expects exactly one).

**Visibility is in the read.** A person reads what they created plus what was made in a room that names
them; the rooms come from the current policy at the moment of each request (`rooms_named`), so removing a
person from a room revokes access when the policy is reloaded (`policy.toml` is read once at startup, so in
practice at the next restart, not at the next request), and a `persons = "*"` room names nobody. A document
you may not read is `404`, never `403`, and is indistinguishable from an unknown id. The room's chat id is
never in the payload, only its platform.

**Media, settling §16's two open questions for inbound photos.** An inbound photo is recorded as a
sha256 in `messages.media_json`, which is what `[image a3f2c1: …]` renders from; it is resolvable, and it
carries its room because the message row does. So a code resolves against the document's own room's
message rows through the shared resolver `room_sightings` (DEC-251, operator 2026-10-01, amending DEC-138:
the `recent` window plus the room's last 90 days of message media rows, keyed by the message row), plus
media-index rows born in the room (`provenance.platform` and `provenance.conversation_id`; any source).
Images only is this block's own rule: a clip, a voice note and a shared item's cover are never a picture.
The message row is the authority for a photo people sent, not the index row: an index row keeps only the
FIRST room a byte string was seen in (first source wins), so a photo re-sent to room B would be wrongly
refused by provenance. A row with no room (a sticker, a comprehension-born row) never resolves: fail
closed. A code that matches no picture is `media_unknown`; one that matches more than one is
`media_ambiguous` and names the candidates' 8-character handles; nothing is ever picked. A photo older
than 90 days is not found, and a scan cut short by the row cap that finds nothing answers `unavailable`
(could not check that far), never `media_unknown`.
Each Image's code is then **rewritten to the full sha256** and the document is checked again, so what is
stored is what was judged. The index half is the sandbox session's `resolve_room_media` (its #649), used as
is for each of the room's ids: a code resolves only in the room it was born in whatever its source, and an
index that cannot be read is `unavailable` (nothing saved), never "no such picture"; so there is one resolver.

**Revisions.** A revision must be made in the room the document was made in, so a room cannot overwrite
another room's document; from any other room the answer is `no_such_document`, the same as for an
id that is no document at all, so an id never confirms that a document exists elsewhere. Anyone in that room may revise; the
creator stays the creator. The Page title is the document's title (no separate `title` argument, which
serialises one fact once).

**Review of PR 654 (2026-10-01), what it changed.** Publishing needs a known person (`unknown_person`);
an edit must name the revision it read (`base_revision_required`) and a stale one is a `conflict` that says
which revision is current, so an old edit can no longer win as the next revision; every stored media entry
is a full 64-hex sha256 and a revision is at most 1 MiB of JSON, both enforced by a CHECK in the database as
well as in the service; the list endpoint clamps `limit` and `offset` (and the page number is capped at
100,000) so an absurd value answers a page, not a 500. A room is matched by its conversation id alone, not by
platform, because conversation ids are unique across platforms by the same convention `media_fetch` relies
on; if that ever stops being true, the room key here must gain the platform. Not covered: `nora person
forget` does not reach documents, and a message the platform deleted may still be resolvable by `recent`;
these are issues #655 and #656.

**Not built, on purpose.** The media byte route (W5, with the portal), the tool and its routing eval
(W4b), search, the `path` form of publishing (it waits on the sandbox session's audited reader). The
migration is `0084_documents`, after the sandbox session's `0083_computer_exports` (#649).

## 26. W4b: the tools the model publishes with (2026-10-01)

Two tools over the W4a service, both in `nora/agent/tools.py`, both withheld from every turn unless
`[documents] enabled` (ships false, like `[record_query]`): the portal pages that open a document are a
later step, so a model that published one now would hand people a path that shows nothing.

**`document_publish(spec, document_id?, base_revision?)`**, level 3, budget 4, an act (not read-only,
`non_idempotent`, never retried). `spec` is the JSON object, or the same as text. It calls
`DocumentService.publish` with the requester's person id (the room's tagger); a turn with no tagger (a
fire, a Dreamer pass) stores nothing and says so, because a document is published for the person who asked.
The catalog is learned from the tool, not from memory: a call with no `spec` answers `bad_arguments` with
the generated catalog prompt (`DocumentService.catalog`) in a `catalog` field, paid only by the call that
needs it and never by every turn. The MUST rule (a document is a JSON spec, never HTML, markup or a file)
is in the description, with one line in `conduct.md` (version 34) that names no tool and forbids nothing
when the tool is absent, because doctrine is bind-mounted and goes live at the next pull, before the image.

**Statuses, and why these words.** `ok` carries `document_id`, `revision` and the portal `url`, and a note
that says it is under Documents for people signed in and not a public link. A refusal with findings, a
missing or stale `base_revision` and a malformed id are `bad_arguments` (a mistake in the call, correctable
on the next turn; `refused` is R-31's disclosure veto and would read as «I may not say»). A document of
another room is `not_found`, the same as an unknown id. A store or checker that failed is `unavailable`:
nothing is known to be stored.

**`document_read(document_id)`**, level 1, budget 2, read-only. Editing needs the current spec and revision,
and a model does not remember either across turns, so this is the minimum that makes `base_revision`
usable. A spec over 40,000 characters of JSON is named and left out (never cut in half), with the revision,
so the model can republish the whole document against it.

**Not built.** The `path` form (waits on #649's audited reader). No routing result: the `route_document_*`
cases are in `tool_selection`, the two positives need a subject with `[documents] enabled` (the eval
subject is the deployment's config, which has it off), so they are NOT RUN; the three negatives (a count, a
picture, a CSV file) pass trivially while it is off. The skill that teaches composing a good document, the
portal routes and the media byte route are W5 and after.

## 27. W5: the portal pages and the picture route (2026-10-01)

**Pages.** `/documents` lists what the viewer may read, newest revision first (title, who asked, the
room's platform, how long ago, a revision badge when it is past the first), with previous/next paging.
`/documents/<id>` draws the stored spec through the same registry the fixtures use, in the portal theme;
the spec's own Page draws the one `<h1>`, and a line above it says who made it, where, and (when revised)
the revision and when. A document the viewer may not read, an unknown id and a string that is not an id all
show one «Not one of your documents» page, because Nora answers the first two alike and the portal's proxy
refuses the third before asking. A stored spec this build's catalog refuses is said so, never half drawn.
The revision history is not shown: the read API returns the current revision only, and a history view
would need its own route (`DocumentStore.history` exists); not built.

**Nav.** One link, «Documents», in the rail's main group (`MAIN_NAV`, no `mobile` flag): the phone's bottom
bar stays four destinations (DESIGN.md §7), and the command palette lists it with the others. No DESIGN.md
change. (Decision for the operator: whether documents deserve a bottom-bar slot on a phone, which would
displace one of Today, Map, Plans or Games.)

**The picture route.** Nora: `GET /app/v1/documents/{id}/media/{sha}?personId=` (`build_document_media`).
It answers a picture only when ALL of these hold, and every other case is one `404`: the document is
readable by that person (the same query as the detail route), the sha is named by the revision being read,
the bytes are in the blob store, and the bytes THEMSELVES are a PNG, JPEG, GIF or WebP (read off the first
bytes, never the media index's label or the sender's, so an SVG, which can carry script, is refused however
it was labelled). A blob store that cannot be read, or none configured, is `503` «could not check», never a
`404`. It is sent with `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src
'none'`, `Cache-Control: private, no-store` (a picture kept for minutes would outlive a sign-out on a shared
browser profile and a room removal; found by the review) and `Content-Disposition: inline`. The portal:
`/api/v1/documents/<id>/media/<sha>` (more specific than the JSON catch-all, which never carries bytes)
checks the session, refuses any id that is not a UUID or sha that is not 64 lower-case hex before making a
request, sends the SESSION's person id (never the browser's), passes on only a raster content type, repeats the headers, and reads a `400` from Nora (the two sides
disagreeing about who the viewer is) as `503`, never «not found». `Dependencies` gains `blobs` for this (57 fields).

**Not built here.** Search (⌘K, trigram), the skill (§28), revision history. `[documents] enabled` stays off:
until it is on, nothing publishes, so the pages are empty for everyone.

## 28. W7: the `document-creation` skill (2026-10-01)

**Placement (ADR 0016 §1).** The must-have rule (a document is a JSON spec, never HTML) is in
`document_publish`'s description and in one `conduct.md` paragraph, so it holds on a turn that never
loads the skill. The skill is judgement for a domain most turns do not touch: whether something is a
document at all, how to write one for a reader who was not in the chat, where pictures and numbers come
from, changing one safely, and what to tell the room. It does not restate the catalog: the no-spec call
of `document_publish` returns the components and a complete example, so the skill tells her to read
that first and carries no example of its own. **No named scenario** (the skill is checked against
`trip`, `itinerary`, `holiday` and the like in `tests/test_skill_content.py`): the catalog's own example
is where a concrete document lives.

**One skill each** (coordinator, 2026-10-01): `computer-use` is the computer's, this one is the
document path's, never combined. The skill says a document is never a file and the computer never
publishes a page; the computer's skill says the reverse.

**The pointer.** `document_publish` now says «Load the document-creation skill before you write one»;
`tests/architecture/test_tool_text_names_only_shipped_skills.py` holds that the skill ships. `skills`
stays at budget 2 (a message spanning three domains is the measured reason to raise it, not the
directory size).

**Cost, stated.** The skill is listed in her `<available_skills>` block whatever `[documents]` says,
as `computer-use` is whatever `[computer]` says: about 78 tokens a turn while documents are off.
Withholding a skill with its feature would change the shared factory and the portal's skill list;
not built, because the listing alone does nothing without the tools.

**Eval.** Two cases join the five: a short question that stays a chat answer, and `document_read`
before any `document_publish` when changing a document (an ORDER criterion, on an id no sandbox holds,
so it measures reading first, not finding the id). The subject for the documents-on arm is the
deployment's config with `[documents] enabled = true`, passed as `--deployment-config`: nothing in the
live config changes and `materialise()` still compares the generated config with the file it was given.

## 29. The `<documents>` block: how she learns a document's id in a later turn (2026-10-01)

**The gap.** `document_read` and `document_publish` take a document's id, and a revision asked for
later ("make it 8pm") arrives in a turn where the id is nowhere in view. The coordinator's
recommendation, adopted: not a list tool, because a lookup she must remember to call is skipped (an
optional act was chosen 3 times in about 720 replayed turns), while a line already in view is not. It
is what `<computer>` does for its export links.

**The block.** `<documents>` is rendered by code (`render_documents`), never by a model, from
`DocumentsContextReader.read`, which calls the room-scoped `DocumentStore.list_room`: this ROOM's
documents (every conversation id the room answers to, on its platform), most recently revised first,
at most five, each as title, id, revision and age. It is the room's own list and not a person's, because
everyone the room names already reads these; a document made in another room never appears, whoever made
it. No spec or text is loaded (a turn reads this), and the top-revision subquery is restricted to the
room's documents first. Order 58, `turn`-keyed, directly after `<computer>`: its ages move every turn.
It renders only under `[documents] enabled` (the reader is built under the same flag that withholds the
tools) and only when the room has a document; otherwise it costs nothing.

**Fail quiet, never wrong.** A store that is down, or slower than one second, is logged, counted
(`nora_errors_total{stage="documents_context"}`) and read as no block: the turn is never held or failed,
and the block never shows an id it did not read. The block's own description says no block does not mean
no document exists.

**What it says.** A title is data a model wrote: collapsed to one line, cut to 50 characters, escaped.
The description tells her to `document_read` then `document_publish` with `base_revision`, and to use an
id only in those calls (a UUID in a reply is blocked at egress; people read titles), matching the skill.

**Cost, measured** (cl100k, 2026-10-01): 150 tokens with one document, 316 with five. The coordinator's
estimate was about 100; the ids are about 27 tokens each and are what the tool takes, so five documents
cannot be smaller than roughly 135 tokens before any title. The description is about 100.

**Eval.** `route_document_revise_id_only_in_the_block`: the document is seeded into the room
(`metadata.documents`, refused on a subject with `[documents]` off) and named nowhere in the message or the
transcript; the case asks for `document_read` on its id. The before arm is the commit that adds the case
and the seeding without the block; the after arm adds the block.
