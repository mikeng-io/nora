# 0020 · Documents are JSON specs the portal renders, not HTML a model writes

- **Status:** accepted as built (2026-10-01, operator): documents are live
- **Date:** 2026-09-30
- **Extends:** [0019](0019-noras-computer-runs-in-an-sbx-microvm.md): its `computer_export`
  publishes through Drop; this changes where a published result goes.
- **Reverses, in part:** the choice recorded in `nora/compute/smith/page_shell.py`'s docstring to
  use plain HTML and CSS with no React or shadcn dependency for what Smith renders.
- **Index:** DEC-258

## Context

Nora's artifacts were HTML that Smith, a separate agent, wrote by hand and published to Drop,
which serves it on a public URL. Read from the tree and from norty on 2026-09-30
([design/artifact/00-design.md](../../design/artifact/00-design.md) §3, §17):

- **Style drifts per run.** `page_shell.py` holds the only shared tokens, a second copy of the
  theme the portal already owns, and each stored document carries its own 1.1 to 5.8 KB of CSS.
- **Output tokens are the binding ceiling** (12,000 per tag turn, silent). Hand-written markup and
  inline SVG spend it on structure.
- **Drop serves `text/*` inline** (`deploy/drop/rendering.py`), so an HTML artifact runs as a live
  page with no CSP. Its only isolation was being its own origin.
- **The corpus is small.** The `artifacts` table holds 15 rows; 4 are real HTML documents, the rest
  are test fixtures and uploads. None contains a script, an external host or an `<img>`.
- **`@json-render/*` 0.21.0** supplies a catalog, a registry and a renderer. Its own
  `catalog.prompt()` is 17,048 characters and teaches `repeat`, `$state` and actions; its
  `jsonSchema()` leaves `props` as `additionalProperties: {}`, so it validates no prop. Its shadcn
  components passed `className` from the model into the DOM, hard-coded colours this theme removes,
  and broke DESIGN.md's Hairline-First rule.
- The operator ruled on 2026-09-30: the agent emits a JSON spec, never HTML; documents open only
  behind the `identity.toml` sign-in, are listed and opened in the web app, reuse its theme, and
  are called "documents".

## Decision

1. **A document is a JSON spec** `{version: 1, root: <Page>}` over a **closed catalog** of 21
   components (`app/src/lib/documents/catalog.ts`). It is stored as a nested tree, which cannot have
   a dangling reference, a cycle or an orphan, and whose element keys are only `type`, `props` and
   `children`, so `on`, `watch`, `repeat`, `visible` and `state` cannot be written.
2. **The catalog is the one definition.** The renderer, the JSON Schema Nora's checker validates
   against (`nora/documents/document.schema.json`) and the prompt fragment the skill embeds are all
   generated from it, and a test fails when either file is stale.
3. **Every prop list is strict.** No component takes `className`, a style or raw HTML. A link is an
   http(s) address in `Sources`, or in Markdown text where it costs a click and nothing more. A picture
   is named by a media code and resolved to a sha256 by Nora, never a URL. A chart has one axis. A
   diagram's source is plain drawing instructions, accepted by an allowlist (it starts with a known
   diagram keyword, has no control or non-ASCII space character, no backslash, no `%%{` or `@{`, no
   `click`/`link`/`callback`/`call`/`href` statement, only plain values on `style` lines, no `url(`)
   because Mermaid 12 reads configuration, node images and CSS from those and draws into the live page,
   so a request happens before any output check can run. A blocklist was tried first and a second
   review walked round it.
   Nesting is capped at 8 levels and a document at 500 nodes.
4. **Documents render inside the portal**, behind sign-in, on the portal's theme, through
   `@json-render/core` and `/react` with components written on the portal's own primitives.
   `@json-render/shadcn` is not used.
5. **Drop's artifact half is retired** once the portal serves documents; Drop's link shortener stays.
   This decision does not retire it: that is a later change. As built (DEC-254, 2026-10-01), Smith,
   `make_artifact`, Drop's token mint and the `nora-compute` network are gone, but Drop's artifact read path
   and its `POST`/`PATCH /api/artifacts` stay, because `computer_export` publishes FILES (never a page)
   through them and old links keep opening. What is retired is the document use of Drop.

## Consequences

- Style cannot drift: the model picks components from a closed set and the look is the portal's.
- The model spends tokens on content, and the prompt fragment is about 1,700 tokens.
- A public URL becomes a signed-in one. The old artifacts were reachable by anyone with the link;
  documents are not.
- Documents render on the portal's own origin, next to the session cookie, so a rule that lets a
  model emit markup or a link is a stored-XSS hole, not a style flaw. The schema and the checker are
  security boundaries.

**Worse**

- **A second place to keep in step.** The catalog changes in TypeScript and its derived files are
  committed under `nora/documents/`; the drift test runs in `npm test`, which `make gate` does not,
  so a stale file is caught only when someone runs the app's tests.
- **A pre-1.0 dependency** (`@json-render/core` and `/react` 0.21.0) sits under every document.
- **A heavier portal.** `mermaid` and MapLibre load on demand, but the portal now ships both.
- **Old public links stop being the model.** The 4 existing HTML documents were left readable on Drop
  (DEC-254 keeps its read path), so they stay public and unauthenticated until they are retired or expire.
- **Documents are live, so a bad one is a live defect.** The checker, the store, the read API, the tools, the
  portal pages, the skill, links and archive, delete and restore are built and in use (#644, #648, #654, #658,
  #660, #667, #668, #670, #673, #674). The renderer still validates every document before drawing it, so a
  bad one is reported, not shown.
- **Not built.** Search, a phone bottom-bar entry, a present view, print/PDF and a revision-history view are
  planned (`design/artifact/00-design.md` §32); `nora person forget` does not yet reach documents (#655).
- **The portal sends no Content-Security-Policy** (`app/next.config.ts`). Documents rely on the
  schema, the renderer's own checks and the Mermaid source rule; a CSP for the document route would
  be a second wall and is not built here.
- **Two validators read one schema.** Zod validates in the app and Python's `jsonschema` will in the
  checker, and their regex engines differ (`$` before a trailing newline, `\d`). One corpus is run
  through Zod, ajv and Python, and the schema uses `(?![\s\S])` and `[0-9]` so they agree.

## Rejected

- **A public HTML viewer on Drop, as today.** Keeps the drift, the token cost and the unsandboxed
  origin.
- **Markdown only.** Easiest for the model, but it cannot carry a map, a chart or a timeline that a
  checker can validate. Markdown stays as prose inside a `Markdown` component.
- **XML.** Nothing in this stack reads it.
- **json-render's flat id map as the stored form.** It allows the three structural faults a tree
  cannot have and spends tokens on ids; the renderer converts at render time.
- **The shadcn component package as the look.** Measured on the portal theme it broke the
  Hairline-First rule and several defaults; none of the 21 components needed it.
- **Embeddings for document search in the first version.** The `embedding` table has had no writer
  since the knowledge tier was deprecated (2026-09-07); trigram search is measured on Cantonese and
  English, and semantic search waits for a measured miss.
