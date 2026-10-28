---
type: design
title: "Documents: json-render specs, stored by Nora and opened in the portal"
description: "Built and live in part. What Smith called an artifact is a document: a small JSON spec over a closed catalog of 24 components (19 after §42 removed Tabs, five added in §43) (json-render's catalog and renderer on the portal's own primitives, no shadcn components), validated in Nora's container, stored in Postgres, listed and opened inside the signed-in web app with the portal's own theme, with archive, delete and restore. Drop's artifact READ path and its POST/PATCH /api/artifacts stay for computer_export links and old links (DEC-254). Search, a phone bottom-bar entry, a present view, print/PDF and a revision-history view are built (§33, §34). The sandbox side (files only, never a page; make_artifact and Smith retired) belongs to design/sandbox-runtime."
tags: [nora, documents, artifact, json-render, react, portal, drop, skill]
timestamp: '2026-10-01'
status: partly-built
---

# Documents

> **Amended 2026-10-01 (coordinator decision, sandbox session; Miles's ruling).** Publishing a document is
> **`document_publish`, a document tool of this session's** (as built: `document_publish(spec, document_id?, base_revision?)`, §26; the `path` and `title` forms of this note were not built), not
> `computer_export(to="document")`. `computer_export` never had that option and never publishes a page: as of
> the sandbox session's #649 `html` is refused and `link` only carries files (CSV, PDF, PNG); design/sandbox-runtime
> §6 records it (#650). `path` is read through that session's audited workspace-read helper, never through
> `SandboxRuntime.fetch` directly (that would bypass `computer_events`, the room lease and the scope derivation),
> and the inline `spec` form is built first. The must-have rule lives in `document_publish`'s own description and
> in doctrine. This note supersedes every `computer_export` mention below that predates it; the older text is
> left, marked, where it records how the design got here.

Status (2026-10-01): **built and live in part.** What exists, by section: the catalog and renderer (§17 to §21,
#644), the checker (§24, #648), the store, publish service and read API (§25, #654), the tools (§26, #658),
the portal pages and picture route (§27, #660), the `document-creation` skill (§28, #667), the room's
`<documents>` block (§29, #670), the sightings resolver for pictures (#668), links (§30, #673) and the
lifecycle of archive, delete and restore (§31, #674). **Built since** (§33): search, a phone bottom-bar entry, forget reaching documents. **Built since (§34):**
a present view, print/PDF and a revision-history view. **Later still:** web pictures (§35), the first live read-back (§36),
the link guard and minted links (§37 to §39), Map by name (§40), one home for each rule and the not-saved note (§41),
and `Tabs` removed (§42). A statement below that says one of these is not built or is read-only is marked where it stands. **Parked:** long background research reports,
which are the job of the future `delegate_task` (open issue #22, written while Smith still existed, so its
wording is stale), not of documents. The sections below are in the order
they were written: where an early section says something the later ones or the code changed, it carries a
marker, and the code wins.

## 1. Decisions already made (operator, 2026-09-30)

1. **An agent emits a JSON spec, never HTML, CSS or JS.** A fixed renderer turns it into a page.
2. **Built on React, shadcn and json-render** (`vercel-labs/json-render`). *Narrowed while building (§18,
   ADR 0020 decision 4):* shadcn is NOT used. `@json-render/core` and `/react` stay, and the components are
   written on the portal's own primitives; `@json-render/shadcn` was removed.
3. **Documents open only behind the portal's sign-in** (`identity.toml`). There is no public link.
4. **They are listed and opened in the web app**, not on a separate surface.
5. **They reuse the portal's whole style and theme.**
6. **The word is "document"**, not "artifact".

Decisions 3 and 4 remove the reason Drop's artifact half exists (§2), so this design retires it.
*As built (2026-10-01):* not retired yet. `computer_export(to="link")` still publishes files through Drop's
`POST`/`PATCH /api/artifacts`, and old links keep reading (DEC-254); only the document use of it is gone.
**Superseded (ADR 0023, 2026-10-08):** Drop, its artifact half and its shortener are all retired; a link export now opens in the portal's `/artifacts` page behind sign-in, and old Drop links stop resolving.

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
>
> **As built (§26, §30):** `document_publish(spec, document_id?, base_revision?)`. There is no `path` form
> (the audited helper, `WorkspaceReader`, existed from #649 and nothing called it for a document; deleted in §41) and no `title`
> (the Page's title is the document's title). An edit names the revision it read. The result carries
> `document_id`, `revision` and, when `[documents] public_url` is set, a `url` made from a 12-hex handle;
> with it empty there is no `url` at all. Beside it: `document_read(document_id)` and
> `document_set_status(document_id, state)`.

## 3. Why the present shape failed (read from the tree, 2026-09-30; Smith and `make_artifact` were retired 2026-10-01, DEC-254)

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
  different feature and stays. **Superseded (ADR 0023, 2026-10-08):** the shortener is deleted too, and `maps_url` is the full Google Maps URL.

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
  │  GET /web/documents, /web/documents/<id>          ← as first designed; built as /app/v1/documents* (§27, §34)
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

*The table layout below is the study's. As built it is three tables, `documents`, `document_revision` and
`document_state` (§25, §31); the spec lives on the revision, not on `documents`.*

- **Table `documents`**, a new one through Nora's alembic chain (Nora owns the schema, as she
  does `artifacts` today):
  `id` (uuid), `title`, `spec` (jsonb), `spec_version`, `sha256`, `room`, `created_by`,
  `turn` (provenance), `created_at`, `updated_at`, `revision` (int).
- **Revision keeps the id.** Updating replaces the spec and bumps `revision`; the link never
  changes. Keep prior revisions? Open decision 4.
- **Specs are small** (KB), so they live in Postgres. RustFS is not involved unless a document
  embeds an image (open decision 5).
- **Nora is the only writer.** The web app is read-only, matching the proxy's GET-only table. **Superseded (§31, §34):**
  the portal also writes a document's state (`POST /app/v1/documents/{id}/state`) and restores a revision, each on its own route.
- **Retired, as built (DEC-254):** `/api/tokens` and the `nora-compute` membership, with Smith. **NOT
  retired, and the study's list was wrong about them:** the `artifacts` table, Drop's `POST`/`PATCH
  /api/artifacts`, `DropArtifactIngest` and the `ArtifactIngest` port, because `computer_export`
  (`to="link"`) is their caller (S3a, #649). The 4 real HTML artifacts (and 11 other stored files) were
  open decision 6, now settled in §12. **Superseded (ADR 0023, 2026-10-08):** all of these are deleted with Drop, except the `artifacts` table, which stays in place, unread.

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

## 9. Catalog, first cut (superseded: 21 components, §17, §19, §21; `Chart` is bar and line only)

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

**Status (2026-10-01).** The steps were built under the names W1 to W7 and a lifecycle step (§17 to §31), not
S0 to S6. S6, retiring Drop's artifact half, is NOT done and is not wanted yet: `computer_export` publishes
files through it (§5). **Superseded (ADR 0023, 2026-10-08):** S6 is done, as a whole-service retirement. The `path` form of `document_publish` was not built.

## 12. The decisions that were open (settled by 2026-10-01)

Each was open on 2026-09-30 and has since been settled, by the later section named or by a ruling. Nothing
here is open except decision 9.

1. **Who can see a document: settled (§25).** A document is visible to **its creator and to the persons a
   room it was made in names**, computed at read time from the live policy holder (valid `policy.toml` edits
   apply within three seconds without restarting). A `persons = "*"` room names nobody, so
   there only the creator reads it. Everyone else gets 404, never 403, the same answer as an unknown id.
   What the study recommended, restated: `identity.toml` maps persons to *accounts* only; room membership
   lives in `policy.toml`, `[[rooms]] persons = [...]`, and `nora/web/memory.py` already scopes a read by
   viewer through `workspaces_of_person`; documents needed the room-level analogue.
2. **Spec authority: settled (§18).** A Nora envelope, `{version: 1, root: <Page>}`, stored as a nested
   tree, with no `title` of its own (the Page's title is the document's title).
3. **Nav placement: settled (§27).** One «Documents» link in the rail's main group and the command palette;
   the phone bottom bar was four destinations (DESIGN.md §7). **Ruled 2026-10-01 (§32), built (§33):** a phone
   bottom-bar entry; the bar now holds five and DESIGN.md says so.
4. **Keep prior revisions: settled (§25).** Yes. `document_revision` keeps one full snapshot per revision,
   append-only. **Ruled 2026-10-01 (§32), built (§34):** a view of that history and a restore.
5. **Images inside a document: settled (§16, §25).** Room media by reference (a media code resolved to a
   sha256 by Nora), never a URL; no external images in v1. **Reversed (§35, DEC-269):** a web picture she looked at with
   `media_fetch` is kept and named by its code, never by its URL.
6. **The 4 existing HTML artifacts: settled (DEC-254).** Drop's artifact READ path and `POST`/`PATCH
   /api/artifacts` stay, so every old link keeps working, and Drop's artifact half is not retired (§5). **Superseded (ADR 0023, 2026-10-08):** Drop is retired and those old links stop resolving.
7. **Maps: settled (§14, §21).** A custom `Map` component over the portal's MapLibre, with coordinates and
   never an address. **Amended (§40, DEC-272):** the model writes place names only and the service looks the
   coordinates up.
8. **Which session is the sandbox session: moot.** The §2 contract was amended 2026-10-01 and the sandbox
   side landed (#649, #672).
9. **Render Mermaid in the sandbox instead of the page: still open (§22).** Recommended not yet. The
   allowlist and a pinned mermaid version are the defence; the trigger to revisit is a third bypass or the
   audience widening beyond the rooms a document came from.

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

*Status: the `documents.text` column and the checker's extraction exist (§24, §25); search itself is NOT built
(§25, §27, §31) and is ruled to be built (§32).*

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
a `Deck` is out of the first catalog unless S1 shows a real need. (Settled in §15 as a view of the same
document, and ruled 2026-10-01 to be built; see §32. It does not exist yet.)

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
built later. **Parked (operator, 2026-10-01): long background research reports are that future
`delegate_task`'s job, not documents'** (open issue #22); nothing in the document
path is waiting on it.

**Presentation is a view of the same document, not a second format.** The portal gets a
`?view=present` mode: each top-level `Section` becomes a slide, with full-screen and keyboard
navigation. So no `Deck` component and no slide format. The cost goes into the skill: one idea per
top-level section. This closes open decision 8 (slides). **Not built (2026-10-01):** there is no present
view and no `?view=present`; it is ruled to be built (§32). Until it merges, nothing may tell a person the
page can be shown as slides, and the skill says only "one idea per top-level section".

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

## 23. No checker in the VM; Tabs on the portal's Radix tabs (2026-10-01; Tabs removed, §42)

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
six writers and expects exactly one). A document's lifecycle (archive, delete, restore) is a third
append-only table, `document_state`, added later: §31.

**Visibility is in the read.** A person reads what they created plus what was made in a room that names
them; the rooms come from the current policy at the moment of each request (`rooms_named`), so removing a
person from a room revokes access after the next valid policy reload (within three seconds, without
a restart), and a `persons = "*"` room names nobody. A document
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
forget` did not reach documents (it does now, §33), and a message the platform deleted may still be resolvable by `recent`;
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
displace one of Today, Map, Plans or Games.) **Ruled 2026-10-01 (§32): yes; built, §33.**

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

**Not built here.** Search (⌘K, trigram), the skill (§28), revision history. `[documents] enabled` stayed off
while this section was written, so the pages were empty for everyone. Documents have been live since
2026-10-01 (the first publish, DEC-254's precondition), so that no longer holds; search and the revision
history are still not built.

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

**What it says.** A title is data a model wrote: collapsed to one line, cut to 50 characters, stripped of the straight quote its line wraps it in (review: `x" · id <uuid>` closed the quote and forged an id field), escaped.
The description tells her to `document_read` then `document_publish` with `base_revision`, and to use an
id only in those calls (a UUID in a reply is blocked at egress; people read titles), matching the skill.

**Cost, measured** (cl100k, 2026-10-01): 150 tokens with one document, 316 with five. The coordinator's
estimate was about 100; the ids are about 27 tokens each and are what the tool takes, so five documents
cannot be smaller than roughly 135 tokens before any title. The description is about 100.

**Eval.** `route_document_revise_id_only_in_the_block`: the document is seeded into the room
(`metadata.documents`, refused on a subject with `[documents]` off) and named nowhere in the message or the
transcript; the case asks for `document_read` on its id. The before arm is the commit that adds the case
and the seeding without the block; the after arm adds the block.

## 30. A link to a document in her reply (2026-10-01)

**The gap, measured live.** The first real document was published and her reply carried no link. Egress
blocks any UUID in a reply (`nora/agent/egress.py`, a floor at every exit), the skill told her to name
the title only, and the operator expected a link. The floor stays.

**The handle.** A link carries the first 12 hex digits of the document id, no dashes (`handle_of`,
`nora/domain/documents.py`): it is not a UUID, so the whole link passes the identifier scan and the reply
scan (tested against the real scans over 200 random ids, with a UUID as the control that still blocks).
`[documents] public_url` (an origin only: scheme and host, optional port; no path, query, fragment or
login; default empty) gives the link `<public_url>/documents/<handle>`. **Empty means no link at all**:
the tool result carries no `url`, no path and no host, and its note tells her plainly there is no link
yet and that the document is under Documents by its title; she never prints a relative path or a host she
invented (the skill says the same).

**Resolution is visibility first, then the prefix.** `get_visible_by_handle` (store, fake, and
`GET /app/v1/documents/<handle>`) matches the prefix among the documents the viewer may read and
nowhere else. Zero matches, more than one (a collision is refused, never picked), a handle outside the
reader's visibility and a string that is not a handle are the same single 404 (or 400 for a string that
is neither a handle nor an id, decided before any lookup), so the answer is no oracle for a document the
viewer may not see or for an ambiguity. 48 bits is an address, not a secret: the page is signed in and
the viewer rule is unchanged. A full UUID keeps working; the picture route keeps the full id (the page
passes the resolved id to it). The handle reaches SQL only after `is_handle` (`[0-9a-f]{12}`) and the
LIKE is autoescaped: each is shown load-bearing by reverting it (an empty handle would match every
document).

**Review (independent, scenarios only; seven scenarios held).** Four defects found and fixed here:
`public_url` accepted a control character, a bare backslash, a trailing dot and a port out of range (now a
plain host of letters, digits and hyphens, and a valid port); an origin whose host is UUID-shaped or
key-shaped passes that check but egress blocks the whole link, so every reply carrying it would vanish
(composition now refuses to start on egress's own scan of the longest link the handle can make); a new
document could draw a handle a document its room already reads has, which would 404 both links for the
room (the id is drawn again, a few times, against what that room can read; another room's documents are
none of its business; a clash that still happens is refused, never picked). Left as is, on purpose: the
`document_publish` description still says `ok` "carries `url`", which is false with no origin set; the
result's note says so plainly, and a description change needs a `tool_selection` before and after (ADR
0015), which one phrase does not justify.

**Not changed.** Egress, doctrine, the `<documents>` block's content (its note now says a reply carries
the link a publish returned, or the title). Live step: set `[documents] public_url` to the web app's
origin (its `AUTH_URL`) through nora-deploy step 5.

## 31. Lifecycle: archive, delete, restore (2026-10-01)

**The gap.** A document could be made and revised and never put away: the operator asked for
**archive** and a **restorable delete**. Both tables are append-only by design (§25), so neither gains a
mutable column and nothing is updated.

**Storage.** A third append-only table, `document_state` (migration `0087_document_state`): one row per
change, `(document_id, state, set_by, recorded_at)`, `state` one of `active`, `archived`, `deleted`
(a CHECK), no foreign key for §25's reason, and `nora.infra.sql.history.APPEND_ONLY` names its system
time. A document's state is its **latest row**; no row is `active`, so every document written before
it is active with no backfill. A restore is one more row (`active`). Nothing is ever removed:
a deleted document keeps every revision in `document_revision`.

**What each state does, all in the store's SQL (and the fake's), never in a caller.**
- `archived`: off the portal's default list and off the room's `<documents>` block; listed under the
  portal's Archived filter (`state=archived`); still opens by its link and its id; a revision is refused
  (`RevisionOutcome("not_active")`, `document_not_active` from the service) until it is restored.
- `deleted`: off every list and off the block's live lines; every person-facing read (by id, by a
  link's handle, a picture) is the same 404 as an unknown id, so nothing leaks. Only the publish
  service's own room-scoped reads still find it, which is how a room restores it through Nora.

**Restorable from the chat (review of PR #674).** After a delete nothing a later turn saw carried the
id, so "ask Nora to bring it back" could not work. The room's `<documents>` block now ends with at
most three put-away documents of THAT room (`PUT_AWAY_SHOWN`), each line led by its state
(`deleted · "title" · id … · revision … · age`), and renders nothing extra when there are none (the
one-document block is byte-identical). `document_set_status` also takes the 12-hex handle a link
carries, resolved among this room's documents in any state; a handle matching none or two, like
another room's document, is the same `not_found`. The test that proves it deletes and restores using
only ids parsed from the block a later turn renders.

**Serialized.** `add_revision` and `set_state` both take the identity row `FOR UPDATE` first, so a
revision that read `active` cannot land after a concurrent delete (two-connection tests on Postgres, in
both directions). A new document's handle is drawn clear of every document its room reads in any state
(`find_by_handle`), so a restore never brings back a clash.

**Known and left (review of PR #674).** The portal write is read-then-write with no compare-and-set: two
people changing one document's state at once each succeed in order, and the later row wins, which is
the intended outcome. A document's creator outside its room may change its state, because they may
read it (DEC-255 as written).

**Who may change it.** In the portal, whoever may READ the document (its creator, or a person a room it
was made in names): the read gate is the write gate (`change_document_state` calls `get_visible` first),
so a deleted document is out of reach there. Through Nora, the tool `document_set_status` (`archived`,
`deleted`, `active`) goes through `DocumentService.set_state`, the same same-room rule a revision takes,
for a known person who tagged her; `set_by` is that person. A delete is therefore restorable from the
chat (or the database), never from the portal.

**Surfaces.** `POST /app/v1/documents/{id}/state` behind the portal's own route
(`app/src/app/api/v1/documents/[id]/state/route.ts`; same-origin and JSON only, the session's person,
a UUID and one of three words checked before any request), Archive / Delete (with a confirm) / Restore
on the document page, and a Current / Archived filter on the list. The `document-creation` skill names
the verb in one paragraph so she never hand-rolls a delete as an empty revision.

**Not built.** Search (built since, §33), a purge (a hard delete is a separate decision), and a
portal restore of a deleted document. The decision row is DEC-255.

## 32. Operator rulings of 2026-10-01, and what is planned

Recorded here as rulings; none of the built-or-not facts below changes until the PR that builds it merges.

> **SUPERSEDED 2026-10-03 (guard removal; AGENTS.md «A guard needs a consequence»).** `nora person forget` is deleted. The read side (a `forget:`-marked document stays gone) is kept for any such row already written.

**`nora person forget` soft-deletes every document the person created. There is no purge.** Each of that
person's documents gets a `deleted` row in `document_state` (§31), so it is off every list and every
person-facing read answers 404, and every revision stays in `document_revision`. The unit is the whole
document the person created, including one others have revised since, which is one of the three cases issue
#655 left open. The ruling does not cover a document that quotes a forgotten message in its text, and
nothing is erased for real: DEC-255's reopen condition (a document that must be erased for real needs its
own decision and a purge path) stands. **Built since (§33):** `nora/app/person_forget.py` has the step, and #655 is closed by it.

**To be built, in a separate PR (planned; not built; this design claims none of it exists).** Search and the phone
entry are built since (§33), and the present view, print and PDF, and the revision history since (§34):

- **Search**, v1 lexical over `documents.text` as §14 specifies, behind the same visibility rule.
- **A phone bottom-bar entry for Documents** (§27, and the DESIGN.md change that goes with it).
- **A present view** (`?view=present`, §15).
- **Print and PDF** of a document.
- **A revision-history view** (§27: `DocumentStore.history` exists, the read API returns the current revision
  only).

Until each merges, the `document-creation` skill and the room's tools must not tell a person it exists. The
skill's line about slides was removed on 2026-10-01 for that reason. The generated catalog prompt
(`nora/documents/catalog-prompt.md`, from `app/src/lib/documents/catalog.ts`) still says a presentation view
shows each top-level Section as a slide; it is the one place left that does, and it is generated from the
app, so the present-view PR is where it becomes true or is changed. It is true since §34, and the catalog
line is unchanged.

**Parked, not designed away:** long background research reports. They are the job of a future
`delegate_task` (open issue #22), which delivers a `document_id` through the document tool.

## 33. What the rulings built: forget, reading by link, search, and the portal's rough edges (2026-10-01)

Built from §32's rulings (2026-10-01). Parked and NOT built: a present view, print,
a revision-history view, and `delegate_task`.

**`nora person forget` reaches documents (DEC-264).** A document is the forgotten person's when they
CREATED it (`created_by`); one they only appear in as a participant of its room is not theirs. Each is
soft-deleted through the `DocumentStore` port: one `document_state` row, `deleted`, `set_by` =
`forget:<person_id>`, so a reader can tell the forget from a person's own delete. Nothing is purged and
every revision stays. The command is idempotent (a document already `deleted` is not written again) and its
summary reports `deleted N document(s)` (`would delete` in a dry run) beside the message count.

**It stays forgotten (review of the PR, operator ruling 2026-10-01).** A document whose LATEST state row's
`set_by` starts with `forget:` (`DocumentSummary.forgotten`) cannot be restored or archived by Nora or the portal:
`DocumentService.set_state` refuses it for every caller and state, and a revision of it, answering exactly like an
unknown id (not "restore it first", which would confirm it exists); `<documents>` put-away leaves it out
(`list_room(hide_forgotten=True)`) so its title leaves her context. Only a state row written by someone else, in the
database, brings it back; `unforget` does not. The portal was already closed (`get_visible` hides `deleted`) and
its write refuses it too.

**Race and failure.** `DocumentStore.set_state(only_if_state=)` checks the state under the `FOR UPDATE` lock the write
takes, so forget never overwrites a change made since its snapshot (it counts only rows it wrote and reports the
ones skipped); a failure part-way is printed with what was done and the command exits non-zero, so a re-run
completes it. A two-connection Postgres test covers the wait.

**Search (DEC-265).** The portal's list takes `q`: a case-insensitive substring of the CURRENT revision's
title or search text (migration 0084's `text` column). It is `list_visible(query=)`, not a second read, so
the visibility predicate and the state filter that already hide archived and deleted documents are in the
same SQL statement, and a document a list would not show is never a match (`total` counts the matches).
The query is text: `%`, `_` and the escape character are escaped, inner whitespace is collapsed (in the stored
text too: the checker joins a document's parts with a newline, so the comparison collapses both sides), case is folded
with `lower()` as Postgres does (the fake no longer uses `casefold()`), a blank one is no search, and one over 200
characters or with a control character is a 400 (a NUL straight to Nora's API was a 500). Plain `ILIKE`: the match only runs over one person's
visible documents, so no trigram index and no migration were added; one is the answer if a measured listing
is slow. The portal's list has a debounced search box that keeps the Archived filter. No Nora tool was added
for search: `document_read` and the room's `<documents>` block already cover finding a document from chat.

**Documents carry their room's label.** A row and the detail carry `roomLabel`, the operator's label from
`policy.toml`, only for a viewer whose own rooms include that room (`nora.web.documents._room_label`): a creator
who has since left a room still reads their document, but is not told what the room is called. The chat id is
still never sent.

**`document_read` takes a link's handle.** Like `document_set_status`, it accepts the 12-hex code at the end of a
link as well as the id. `DocumentService._in_room` is the one resolution both share: a handle is matched among THIS
room's documents in any state, none or two matches are never picked, and another room's document, an unknown id and
a deleted document answer alike.

**The portal's rough edges, fixed (2026-10-01).**
- *The title is first.* The detail page hands its meta line and the Archive / Delete controls to the
  renderer as `chrome` (`DocumentView`), drawn right under the document's own H1 inside its Page, and after the
  refusal when a stored spec cannot be shown, so they stay reachable. Nothing sits above the title but the back link.
- *The tab names the document.* A client effect sets `document.title` to `<title> · Nora` (the layout's own template)
  once the document is read and puts the section's title back on leaving. Server-side `generateMetadata` was not used:
  it would need a second signed-in read of the same document just for `<title>`. **Verified, and it did break
  (review):** entering a document whose data was cached rendered at once, the effect ran, and Next then wrote the
  route's own title over it (the tab even read "" while it swapped its `<title>`), so the second visit stayed
  "Documents · Nora". The page now takes its title back while the address is still the document's
  (`shouldReassertTitle`) and never fights the next page's, and an e2e navigates in, out and to another section.
- *A new list is not drawn as the old one.* A new filter, page or search keeps the last rows on screen
  (`keepPreviousData`); while it loads they are dimmed, `aria-busy`, and a status line says the list is updating
  (`freshness`: `loading`). The selected filter is a filled segment, not only `aria-pressed`.
- *A failed refetch that keeps rows says so.* `freshness`: `outdated` shows "May be out of date" with a retry,
  on the list and on a document.
- *The delete panel is keyboard-operable* (`lib/documents/lifecycle.ts`, a reducer the view follows): opening it
  moves focus to Cancel, Escape or Cancel returns focus to Delete, and a finished delete goes to
  `/documents?deleted=1`, where the list says it can be restored by asking Nora in the chat. A delete only marks the
  cached document stale (it does not refetch it), so the page it leaves never flashes "out of date" on its own 404.
  A failed delete keeps the panel open with the error and returns focus into it; an archive or restore that completes
  closes the panel and moves focus to the button that replaced the one pressed. And a 404 on the document's read
  switches the page to not-found even when a cached copy exists (`detailView`), so a gone document never keeps live
  Archive and Delete buttons; only a failure that says nothing about it (network, 5xx) leaves the copy up, marked
  "may be out of date".
- *The row* shows the title, who made it and in which room (the room's label, else the platform), the relative time
  with the absolute date on hover, a revision badge and an Archived badge.
- *Wider.* Both pages use the shell's `default` frame (1080px) instead of `narrow` (760px).
- *The renderer speaks both languages.* The strings `registry.tsx` and `blocks.tsx` owned (aria labels, fallbacks,
  error text) are `documents.renderer.*` in `en.json` and `zh-Hant.json`; a vitest reads both sources and files and
  fails on a key either lacks, on one the source never asks for, and on a hard-coded English string left in the source.
  The checker's own refusal reason (`parseDocument`) is a diagnostic and stays as written.
- *Phone.* Documents joins the bottom bar, which now holds five (`MOBILE_NAV_MAX`); the bar picks its columns from the
  count and a test pins both the count and a label length that fits a 320px phone. DESIGN.md's "four destinations" says
  five at most.
- *The proxy.* `/api/v1/documents` forwards `q` as free text (any script, at most 200 characters, no control
  characters; the other parameters stay id-shaped), because a search is words, not an id.

## 34. Present, print, history (2026-10-01)

**The gap.** §15 promised `?view=present`, nothing said how a document becomes a PDF, and every revision was
already a full snapshot (§25) with no way for a reader to see one. The operator ruled all three in on 2026-10-01
(§32's planned list). Long background reports stay `delegate_task`'s (§15, §32), parked.

**Present (DEC-267).** `/documents/<id>?view=present` draws the SAME stored spec through the same registry,
one slide at a time: slide 1 is a cover (the Page's title block, `data-document-cover`, and every block before
the first top-level `Section`), then one slide per top-level `Section`, carrying any non-Section block that
follows it. A nested Section never splits; each slide is the document with its Page and only that slide's
children, so `parseDocument` checks it like any document. The deck is a modal in effect: it is portalled to
`<body>`, everything else is `inert` while it is open, Tab cycles inside it, and focus returns to the Present
button when it closes. Arrows / space / Page Up and Down / Home / End move unless the focused element owns
them (a table's scroll region, a code block, a diagram, a map, anything that scrolls); Escape and a visible
Leave control always exit; a horizontal swipe moves on a phone; a counter is always shown; the portal's
reduced-motion rule applies. `?slide=` follows the slide by replacing the history entry, so a reload keeps it,
and leaving goes BACK to the page the deck was opened from (or replaces itself with the page), so Back from
the document is never a no-op. Opened from a link's 12-hex handle, everything handed down uses the document's
UUID, so pictures load. A document with no top-level Section shows the ordinary page with a note, and the
Present button is offered only where the split exists. `app/src/lib/documents/present.ts`.

**Print / PDF (DEC-266): the browser's print is the PDF path.** No server-side renderer and no new service: a
headless browser beside Nora would be a second renderer of model-written content to keep in step, a new attack
surface and a container, for a file the reader's browser already makes ("Save as PDF"). The portal adds
`app/src/components/documents/print.css`, active only in print and only on a page holding
`[data-print-document]`: everything outside that subtree is dropped (shell, back link), and inside it the
page's own chrome under the title (§33: the meta line, the outdated note, Archive / Delete, Present / Print /
History) carries `data-print-hide` and is dropped too, so the paper holds the document body only, plus one
printed line where it matters: an old revision says "Revision N of M: not the current version", an archived
document says it is archived. Cards, Tables, Code, Images, Charts, Maps and diagrams are not split across
pages, and a link prints its address only where its text does not already say it. **Paper is light in either
theme, with one approximation:** the tokens are light in print; a Mermaid diagram copied its theme's colours
into its SVG at draw time, so the Print button switches the page to light, waits until every diagram reports
it was redrawn light (`data-mermaid-theme`), prints, and restores the theme; the browser's own print menu
cannot wait, so for it a diagram still drawn dark is inverted (`filter: invert(1) hue-rotate(180deg)`), which
is legible but not the light palette exactly. This holds unless a document must be produced with no reader
present (a scheduled PDF sent to a room), which reopens a server-side renderer.

**History (DEC-268).** Three Nora routes in `nora/web/document_history.py`, through the document's own read
gate (`get_visible`, plus the `forgotten` check `change_document_state` makes) before anything else is read:
the revisions newest first (number, title, sha256, when, who), one revision's stored spec, and a restore. Not
readable, deleted, forgotten (§32), unknown and a revision the document never had are one 404; archived is
readable. Both reads carry `roomLabel` by the detail payload's rule: only for a viewer in that room. The store
gains `revision()` and `RevisionRecord.media`; the picture route also serves a picture an EARLIER revision of
the same readable document names, and a picture no revision names is still a 404. Nora records the tag a
revision was written at (`tag:<id>`), not a person, so the history shows it as hers; a restore records
`portal:<person_id>`, shown by name.

**Restore** publishes an old spec as a NEW revision and never rewrites history. It reuses the store's one
revision path, `add_revision` with `base_revision` (the current number the viewer saw): a revision that landed
since is a `conflict`; an archived document is `not_active`, checked first (so restoring the current revision
of an archived document says archived, not "already current") and again by `add_revision` under its lock; a
document deleted or forgotten between the read and the write is the unreadable 404. The old spec is checked
again with `check_document` (a spec this build would refuse is `invalid`, a checker failure `unavailable`;
neither stores anything). `DocumentService.publish` is not the path: it re-resolves picture codes against the
room's recent messages and is room-scoped.

**Limitation (operator ruling 2026-10-01): editing something out of a document does not remove it from its
history.** Every earlier revision stays readable to anyone who may read the document; the in-group exposure is
minor in this private project, and deleting the document or `nora person forget` hides all of it. No redaction
is built.

**Not built.** A diff between revisions, restoring a deleted document from the portal (§31), redaction, and
opening collapsed blocks for print.

## 35. Pictures from the web in a document (2026-10-02)

**The gap (operator question, 2026-10-02).** Nora could look at a web picture (`media_fetch(url=…)`), but kept
nothing, and a document's `Image` accepts only a media code, so a picture from a site someone collected could
not reach a document. `web_search` and `web_fetch` also never returned a page's picture links, so she met a
picture's address only when the page text happened to contain it.

**Decided (operator, 2026-10-02: «just implement it», plain storage).**

1. **`web_fetch` returns `images`.** Up to 8 http(s) picture links of the page, deduplicated, in the page's
   order, from Tavily (`include_images`) and Exa (`extras.imageLinks`) through one shared cleaner
   (`nora.infra.search._shared.image_links`) so both vendors hand back one shape. Links only: nothing is
   downloaded by the search adapters, so the no-SSRF property of the port is unchanged. `web_search` is not
   changed: Tavily returns search pictures unattached to a result, and a link with no page is no use to a document.
2. **A picture she looks at is kept.** `media_fetch(url=…)` on an image, asked for by the model itself, writes the
   bytes to the blob store and a `fetched` row to `media_objects`, with `provenance = {url, platform,
   conversation_id}` (the room that looked), and returns the row's 6-hex `code`. The download is the
   existing guarded one, and the identifier screen on the URL and the reading is unchanged.
3. **The document needs no new rule.** `DocumentService` already resolves an `Image` code against the room's
   messages and the media rows born in that room, whatever their source (the 2026-10-01 test
   `test_a_picture_born_in_this_room_resolves_whatever_its_source`). A kept picture is such a row.
4. **Not kept:** a clip, a story frame (it expires in 24 hours), and an automatic reading (`instagram_read`, a
   summons). Only a look the model chose is custody.
5. **No code for bytes first indexed in another room.** The first source of a sha256 wins and its provenance is
   never overwritten, so the row stays born elsewhere and would not resolve here. `media_fetch` then returns
   no code rather than one a document would refuse.
6. **Only the types the portal shows.** png, jpeg, gif and webp. An SVG or AVIF would get a code, pass the
   checker and render broken (`app/src/server/document-media.ts`), so it is not kept.
7. **The room that looked owns it** (review of #693). `media_search` (library scope) and `media_send` show a
   `fetched` row only in the room it was born in; what she generated stays global (DEC-138). Without this, filling
   the `fetched` source made every looked-at web picture findable and sendable from any room.
8. **The code works for a follow-up look.** `media_fetch(code=…)` also resolves a `fetched` row born in this room
   (a kept picture has no message row, so the sightings cannot see it).
9. **Credit.** The result's note tells her to put the page under `Sources`; the `Image` caption carries the
   rest. Third-party pictures are kept in custody for this one private group, not republished.

**Not built.** An image-search tool (the discovery path is a page the person already collected), downloading
a picture without reading it, a quota on kept bytes beyond `media.max_bytes` (a picture can be up to the
download ceiling, and nothing deletes kept rows), and withholding the code when the reading was withheld for an
identifier (room photos behave the same way). A story-frame link opened in a LATER turn than the one that read
the story is kept: the story exclusion is per turn.

## 36. The first live document: what the trace showed, and the follow-up (2026-10-02)

**Read-back (trace `1391f819` and its follow-up turn, the build of DEC-269 live).** The pictures never reached
the document, for three causes, none of them in the new code's keep path:

1. **The skill said no.** `document-creation` told her a picture "has to come from this room", so after the
   tools could keep a web picture she answered that web pictures could not go in. Trace `1fdcdb0a`: she loaded
   `media_fetch`, then called `media_search` (the library) and never `media_fetch(url=…)`.
2. **The links were mostly pages.** Tavily's `images` list for four fetched pages held 11 links, about two of them
   pictures; the rest were pages (a hotel listing, related posts) and a Next.js proxy URL. `image_links` now keeps
   only a link whose path ends in png, jpeg, gif or webp, or starts with `/_next/image`.
3. **The document was a note.** Seven sections and about 1,000 characters, in chat register, using Markdown, Table
   and Accordion only. The skill named six of the nineteen catalog components and never asked for depth.

**Decided (operator, 2026-10-02: «everything makes sense»; skills abstracted, not scenario-bound).**
- The skill's picture section now names the three sources of a picture, how to look for them, how to choose and
  credit them. The rule is stated for any subject; `test_skill_content` forbids a named scenario in the body.
- A new section asks for a finished reference (summary first, full substance per section, formal register) and
  another maps each kind of content to its component.
- `test_the_document_skill_names_every_content_component_in_the_catalog` fails when a content component is added to
  the catalog and the skill does not name it, so the drift that left thirteen unnamed cannot recur.

**Review of #696 (independent, told to find the rules it lost): five changes before merge.** The depth rule is scoped
("the full substance the content has", never padded) so it does not collide with «however short it turns out»; «never
guess its code» is restored for the out-of-reach case; the skill now says a coordinate from `where_is`, `where_been` or
`reverse_geocode` is a person's position and never goes in a document (those tools DO return coordinates, for people, and
`Map` accepts any in-range pair); the matching section names `Map` conditionally and `Sources`, and the component test
now reads that section, not the whole file; and `image_links` also admits an extensionless picture whose query carries
a resize parameter or whose host serves pictures (unsplash, Google, Cloudinary, imgix, Medium), because the first filter
silently dropped those.

**Found, not fixed: `Map` cannot be filled** (issue #695). No tool returns a coordinate for a named place (DEC-24,
R-28), so the skill tells her to give each place's `place_info` link in Markdown until a ruling lets a `Map` place carry
a name that `publish` resolves.

**Not measured.** There is no behaviour eval for what a document contains; the `tool_selection` corpus measures routing
only. The check is the live read-back: the same prompt, then the trace.


## 37. A document link must exist, and the turn must be able to publish (2026-10-02)

> **Item 1 below (R-100 as a refusal with a store lookup) is SUPERSEDED by §39 the same day: a link to our documents is
> minted by a tool and a made-up one is removed. Items 2 and 3 stand.**

**Read-back (traces `8dc5551b`, `7983a050`, `1acce721`, 2026-10-01).** Asked for an itinerary document, Nora fetched
pages and looked at four pictures, made fourteen further `media_fetch` calls the per-turn ceiling of four refused, and
ended the turn without sending a spec to `document_publish` (her one call had no spec, which returns the catalog and
stores nothing). Her reply said the document was made, with four photos, and linked a handle that matched nothing. Asked
why it was gone, she called `document_read` with a title, then with the invented handle, got `not_found` twice, and replied
that she had just opened it and it was still there. The next turn admitted it and published a 1,300-character document
with no picture, after skipping the skill.

**Decided (operator, 2026-10-02).**
1. **R-100: a document link in a reply must exist.** `Deliverer` asks `DocumentService.unknown_document_link` for every
   `<public_url>/documents/<handle>` in the reply, rendered and draft form, after R-18. A handle that is in no state in this
   room refuses the reply; a store that cannot be read refuses it too. `send_reply` then returns `refused. <reason>`, the one
   refusal that says why, because a bare `refused` told her nothing she could fix.
2. **`media_fetch` is 8 per turn** (was 4), because a document with pictures looks at one per section.
3. **The catalog carries the rules.** `DocumentService.catalog()` opens with the three rules a skipped skill would have
   given. The skill also says to publish with what she has, to state an assumption instead of holding a document for details
   nobody gave, and that a document exists only once a publish returned `ok`.

**Review of #698 (independent, run against the real pattern and `Deliverer`).** Three changes before merge. The first
pattern used `\\w` and `\\b`, which count a Chinese character as a word character, so a link written with no space beside
Cantonese matched nothing and walked through: the boundaries are ASCII now. The refusal reason was cleared only after R-2,
R-4, R-6 and R-18, so a later refusal by one of those carried the earlier sentence out through `send_reply`: it is cleared
first. A locale-prefixed portal link (`/zh-HK/documents/<handle>`) and a hex run that is not exactly twelve digits (a UUID
without dashes) are now checked too. A new architecture test requires every room-facing `deliver.build` call to be given
the check, since deleting either wiring line had left the suite green.

**Not built.** A check that a reply's claims match the document (it said four photos; the document had none), a check on
a link to another host, and any eval of document content. A rate limit on kept pictures is still open (§35).


## 38. A long spec that is not JSON, and what the room hears (2026-10-02)

**Read-back (trace `c3619636`, 03:25 HKT, the first document turn after §37 deployed).** The behaviour §37 aimed at
worked: she fetched six pages and six pictures (eight are allowed now) and drafted a full reference. Her
`document_publish` failed: she sent `spec` as a 10,000-character string of JSON, broken at character 7,247 by what
the findings called only «Expecting ',' delimiter: line 1 column 7248». With nothing to fix she wrote the draft to her
computer and replied that the document was made, with an invented link. R-100 refused that reply (§39 later replaced the refusal with removing the link); it was her final
text, not a `send_reply` call, so there was no refusal reason to hand back and the room received the generic
«can't manage right now» fallback. The guard held; the room's experience was a brush-off.

**Decided (operator, 2026-10-02).**
1. The `json` finding shows the 28 characters either side of the break, marked `<<HERE>>`, and names the usual cause.
2. The tool's note for a non-JSON spec says to send an object, fix the marked place, and that no document or link exists
   until a call returns `ok`. The skill says «send `spec` as an object, never as text».
3. The string form of `spec` stays accepted.

**Found, not changed.** When R-100 refuses her FINAL text (no `send_reply` call to coach), the room gets the canned
apology rather than a sentence that says the document was not saved. A code-authored honest sentence for exactly that
case is the likely next step, and it is a separate decision. Her Standing note also recorded the document as «published»
after her own false claim, which will need the notebook to stop folding an unsent reply as fact.


## 39. A link to our documents is minted, not authored (2026-10-02)

**Why §37's guard was replaced.** R-100 as first built refused a reply that linked a document that did not exist: a
store lookup in `Deliverer`, a refusal reason passed back through `send_reply`, wiring through the registry,
`deliver.build` and two callers, and an architecture test to keep the wiring. It was 15 files and about 460 lines for
one idea. A review found two bugs in it. In use it was worse for the room: her final text after a failed publish was
refused, and the room received the canned «can't manage right now» apology (§38). The operator asked for the guard to be
simplified from first principles.

**The one fact.** Only `document_publish`, `document_read` and `document_set_status` return a document URL, and the
`<documents>` block she reads carries a title and an id, never a URL (`nora.language.documents_render`). So she never needs
to write a link; she only ever copies one she was handed.

**Decided (operator, 2026-10-02: «let go for C»).**
1. `LinkCarry` (DEC-89's carrier, already shared by the tools and `Deliverer`) is armed with `[documents] public_url` when a
   documents tool is built, so the rule holds even in a turn where no publish succeeds.
2. In `apply`, which already runs before R-6 and R-18, a document link whose handle no tool returned this turn is removed
   (a Markdown link keeps its label), and the link of each document published this turn is appended unless the reply carries
   it. A read or a restore only lets her repeat the link.
3. A reply that was only a made-up link is empty afterwards and is refused as `empty_reply`; nothing blank is sent.
4. The skill's «tell the room where it is» section shrinks to one instruction: the link is added for you, never write one.

**What is gone.** `unknown_document_link`, `refusal_note` and its stale-reason bug, the `send_reply` reason branch,
`own_links` on `Deliverer` and `deliver.build`, `ToolRegistry.own_link_check`, the two caller lines, and the architecture test
that guarded them. The skill body went from 1,495 to 1,468 tokens, which the parked `Map` change needs.

**Not built.** A check that her prose claim «the document is made» matches a publish. The link is the receipt: with no link in
the room, nothing was saved. Re-linking an older document needs a `document_read` first.


**Review of #701 (independent).** Two blocking findings, both fixed. The link was appended to every message of a turn,
the canned fallback included: a message that carried it now settles it (`LinkCarry.mark_sent`), and one due but not sent
stays due. And the skill said never to write a link while a `document_read` result let her pass one on: the skill now says
to repeat a `url` exactly as a document tool returned it this turn, and `document_read` says so. Also fixed: the recorded
copy of a GIF caption kept a made-up link (`LinkCarry.scrub`), the `appended` counter moved for a document link (now
`document_added`), the removal reflowed her whole reply (now only the blank lines it leaves), and `www.`, an explicit port
and a locale with more than one subtag were not matched. Left alone, as adversarial or unlikely: percent-encoding,
zero-width characters in the host, a handle shorter than six digits.


## 40. A Map names its places; the service looks them up (2026-10-02)

**The gap (issue #695).** The `Map` block took `places: [{name, lat, lng}]`, and nothing Nora can call returns a coordinate
for a named place: the resolver holds `lat`/`lng` as working state that DEC-24 keeps out of every model-facing schema, and
`place_info` builds its map link from the asked name, never from a coordinate (R-28). The catalog told her to «look an address up
first, then write its lat and lng», which no tool does. Her first itinerary (2026-10-01) used no Map. Until now the skill told her to
list each place with its `place_info` link.

**Decided (operator, 2026-10-02: «name only»).** The model writes a place's name and nothing else; the service finds where it is.
1. `DocumentService.publish` calls `_ground_maps` after the picture codes are resolved. Every distinct place name goes through
   `screen_and_locate_place`: the outbound screen `place_info` uses (identifier, coordinate, a known person's name: refused before
   any call), `RouteClient.locate`, and the return-path screen on the name. At most five run at once.
2. Whatever `lat`, `lng` or `route` the spec carried is discarded and the looked-up pair written into the stored spec, so a person's
   position from `where_is` or `reverse_geocode` cannot enter a document through a Map.
3. An unknown, ambiguous or refused place is a finding naming it (`place_unknown`, `place_ambiguous`, `place_refused`). A locator
   that is down, or `[route]` off, makes a document with a Map `unavailable`: it is never stored without its coordinates.
4. `document_read` strips each Map's coordinates and route before the model sees the spec; publishing it back re-looks the names up.
5. The catalog makes `lat`, `lng` optional (the service fills them); the portal draws the places that have a pair and lists all of them.

**Cost.** One billed Places call per distinct name per publish, bounded by the 50-place schema cap and the concurrency of five. A
revision re-looks every name up again. No lookup is cached.

**Not built.** A route line between the places (`route` is discarded), a cache, and a way to say which of two ambiguous places was meant
other than naming it more fully.


**Review of #703 (independent, probes run against the PR tree).** Fixed before merge: an address `reverse_geocode` gave out this turn
could still be pinned (the R-57 set `route` checks is now passed to the service); a coordinate written with a space instead of a comma
got through (`looks_like_coordinate` accepts either); nothing bounded the lookups per publish (50 different places in total, names
differing only in case or spacing counted once); the lookups ran before the target's cheap checks (a stale revision paid twice); a document
pushed over the size limit by the coordinates failed as «could not be re-checked» (now a `size` refusal she can fix); 50 unknown places
were 50 findings (10); Google's `could_be` candidates skipped the identifier screen `place_info` applies; and the tool's `person_names`
wiring had no test. Left, stated: other coordinate notations (`N22.3 E114.2`, DMS, plus codes) pass the screen, as they already did for
`place_info` and `route`; `restore_revision` re-stores a spec as it was, and no stored Map predates this change; the tool's wiring of
the live-address set is covered by the service test and the signature, not by driving `reverse_geocode` and then a publish.

## 41. Each rule has one home, and a turn that saved nothing says so (2026-10-02, DEC-274)

**What was measured.** Two Signal turns (traces `c3619636`, `3ba6b39c`) ended the same way: she read the catalog,
wrote the spec to a file on her computer, never sent it to `document_publish`, and told the room the document was made.
The second turn tried to parse the file by hand, spent all twelve `computer_run` calls, and the ceiling sentence ("you
have that context, answer from what you already have") ended the turn. R-100 (§39) had nothing to strip because the
reply carried no link; its own commit names this case as not covered. Four read-only audits then read the model-facing
surface, the backend, the web app and this record.

**What the audits found, in the model-facing path.** Seventeen rules in seven places (the skill, the tool description,
`_CATALOG_RULES`, the catalog, `conduct.md`, `computer-use`, the tool notes); "a document is never a file" and "exists
only once `ok`" each stated four or five times; the skill sent her to her computer for numbers, and `computer-use` called
"making a file people open" computer work, with nothing connecting that file to `document_publish`; the catalog read
spent one of four calls, so a refusal left three tries, each a full resend; the write-tool ceiling told her to answer.

**Decided.**
1. The catalog `document_publish` returns is the one home of how to write a document: the components, the limits (500
   nodes, depth 8, 200 children, 256 KB), the finished-reference rule, sources and figures, and pictures. It is generated
   from `catalog.ts`, so it cannot drift from the checker. `_CATALOG_RULES` is deleted.
2. The tool's description says once that the spec is a JSON object, never a file on her computer, and that a document
   exists only once a call returns `ok`. The skill keeps the process (decide, read before changing, archive, tell the
   room). `computer-use` says to compute on the computer and never to write the spec to a file.
3. `document_publish` has a budget of 6 (a catalog read, the publish, three fixes, one edit).
4. A write tool's R-7 ceiling says only an `ok` call did anything; the generic sentence stays for read tools.
5. `LinkCarry` appends «(No document was saved.)» once to a reply when `document_publish` was called this turn and none
   stored a document, with or without a public origin. It is true whatever she wrote, so it reads none of her words.
   It goes on a message the turn ends on, or on any message after a publish that was tried and refused; a `send_reply`
   after a catalog read alone may be a progress message, so it is left alone (review of #708, which found the false note).
   Not caught: a turn that ends through `send_reply` after only a catalog read, and a failed edit in a turn that had
   already saved a document.
6. `WorkspaceReader` (§26 built it so `document_publish` could read a spec from a file; nothing ever called it) is
   deleted. A `spec_path` argument is the next step only if she still publishes nothing after reading the catalog.

**Review of #708 (independent).** Fixed before merge: the note on a progress message (above); the Map fallback, the
location rule and "look for pictures" were lost with the skill's old sections and are back in the catalog; the tool's
`unavailable` note, which replaced every service note, now carries the Map fallback; the budget comment.

**Not done, and why.** The catalog is not in the tool's description: ADR 0015 caps a Level 3 description at 320 tokens and
the catalog is about 2,000. The picture and Map refusal rounds are not merged: pictures refuse first on purpose, so the
billed Map lookups are not spent on a document that will be refused. `conduct.md`'s one line about documents is
doctrine, so changing it needs the eval suite and is left. DEC-270 (2) and (3) and DEC-271's skill sentence are amended
in part: the rules moved, the decisions stand.

## 42. `Tabs` is removed from the catalog (2026-10-02, DEC-275)

**What was measured.** On 2026-10-02 norty held 4 documents and 7 revisions. `Timeline` was in 4 of 4, `Callout` in 4 of 4,
`Checklist` in 3 of 4, `Sources` in 3 of 4; `Tabs` was in none, and neither were `Map`, `Mermaid`, `Badge`, `KeyValue`,
`Stat`, `Chart` or `Code`. Four documents on one day say nothing about the rarely used components, so none of those
was cut on usage. `Tabs` was cut on cost: the one component with a rule of its own (`Tab` valid only inside `Tabs`,
`Tabs` holding nothing else), which took three extra schema branches in `emit.ts`, a `toFlatSpec` pass to derive the
tab strip, a `Tab` case in the checker's text extraction, six corpus cases and a Radix-backed e2e pair. A document
that wants options side by side uses two `Card`s, and one that wants them folded uses an `Accordion`.

**Decided.** The catalog has 19 components; `Tabs` and `Tab` are refused like any unknown type (corpus case "a Tabs").
The checker, the emitted schema and the prompt are regenerated, and the runbook demo uses two `Card`s. A stored
document that held a `Tabs` would show "cannot be shown"; none exists. The portal's own `ui/tabs.tsx` stays, for its
pages. The renderer no longer shows a reader the schema's raw reason when a stored document cannot be drawn; it says
only that it cannot be shown.

**Considered and not done, with the reason.** `Badge` stays: it is the cheapest leaf and the fixture of about 25
tests. The `spike` page and the demos stay: `app/e2e/documents.spec.ts` drives the renderer's script, image and
`javascript:` sink tests through the spike page, which is a security harness and not a leftover. `Map`, history and
restore, and present and print stay: they are operator decisions of the last two days (DEC-266 to DEC-268, DEC-272) and
four documents give no evidence to reverse them. `Checklist` stays (3 of 4 documents use it).

## 43. A day-of pack: five components, and four that stop being static (2026-10-05, DEC-286)

**What was read.** On 2026-10-04 the operator sent the Ridgeline room a zip of eight spreadsheet exports for a
wedding day (a rundown, a check list, a seating plan, a tea-ceremony table, a photo table, a music list, a thank-you
script and the departure flow) and asked for a page they could open on a phone at the venue instead of a Google Sheet. Nora
built three documents from them. Read back from norty the same day: the three used only `Page`, `Section`, `Grid`,
`KeyValue`, `Table`, `Timeline`, `Checklist`, `Callout`, `Stat`, `Markdown`, `Accordion` and `Sources`, and the two
shapes the sheets are made of, a grid of people against times and a grid of tables of guests, were forced into a six-column
`Table`. The operator said so in the room: the content must be transformed into widgets, with some loss acceptable and not
much.

**What was wrong with what existed**, each read in the renderer, not guessed:

- `Timeline` drew `text` as a plain paragraph, so the `- item` lists Nora wrote in it ran together into one line.
- `Checklist` drew empty squares that nothing could tick, in a document whose use is ticking things off.
- `Table` had no alignment, no totals row and no fixed first column, so a wide table lost its row labels on a phone.
- `Chart` had no share-of-a-whole form, for a budget.

**Decided** (admission by §19: a scenario cannot be written without it, and it has a schema, a renderer, a demo, a
negative fixture and a test):

| change | why a sheet needs it |
|---|---|
| `Timeline`: `text` is Markdown; `people` chips; `tone` | a run of show says who and which moment is critical |
| `Checklist`: tickable on the reader's device (localStorage, never shared), count, `owner`, `title` | four check lists, each by place and person |
| `Table`: `align`, `footer`, sticky first column and header | a cost table with totals |
| `Chart`: `donut` (2 to 8 positive slices) | a budget by item |
| `Schedule` (new): people as rows against one time axis | the rundown's six-column grid |
| `Seating` (new): table cards, seats against capacity, replies | the seating plan |
| `Contact` (new): name, role, a tap-to-call `tel:` link | the day's phone list |
| `Progress` (new): a count against a target | replies in, money paid |
| `Countdown` (new): time left, in Hong Kong time | the day itself |

The catalog is now 24 components. All of it is additive, so `version` stays 1 and every stored document still validates.

**Where the rules live.** The shapes are in `catalog.ts`; the clock and phone patterns are in the schema
(`HH:MM`, and digits, spaces and hyphens with an optional `+`, so a `tel:` link can be built from nothing else). The rules
the schema cannot state are in the checker (`check.py`): a Schedule block that runs backwards, leaves its axis or overlaps its
neighbour in a row, a Progress over its max, a donut with a non-positive slice, a table footer or alignment of the wrong width,
a Seating table over its capacity. The renderer still clamps, so a document that skipped the checker cannot draw outside its
frame. A Schedule's times and a Contact's number are in the search text.

**What the model is told.** One paragraph in the emitted catalog prompt, which is the one home for how to write a document:
a sheet is a source, not a layout; rebuild it for a phone; keep every row a person acts on; and which component each
shape of sheet becomes. The `document-creation` skill is unchanged.

**A ceiling.** A ticked box lives in one browser. Two people on one pack do not see each other's ticks. Sharing them would need
a write path from the portal, which a document does not have (design §32: a document is read-only on the portal). The
reading that would reopen it: two people ticking the same pack and asking why the other's box is empty.

**The fixture.** `demos/wedding.ts` is an invented day-of pack that uses every one of these; no name, number or venue in it
belongs to a real wedding. The source sheets are the operator's and are not in the repository.
