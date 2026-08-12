---
kind: spec
title: "Nora Compute — how an artifact gets produced"
description: "The Drop spec's §7 follow-on: a ComputeBackend port with a LocalDockerBackend adapter, a separate neutral agent (Smith) that runs jobs in an isolated sandbox and registers its own output with Drop, a governed skill library instead of raw code-exec-does-everything, and the update_artifact path Drop's v1 didn't have."
tags: [nora, spec, compute, drop, smith, sandbox, artifacts, local-first]
timestamp: '2026-08-12'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-12-nora-drop-design.md
  - design/proposed/15-gif-replies.md
  - nora/domain/ports.py
---

# Nora Compute

## 0. What this document is

The Nora Drop spec built the delivery half of "Nora can hand someone a file, not
just words" — where an artifact lives, how it gets a URL, how that URL reaches a
person. It deliberately left open how an artifact gets *produced*, naming that
gap as Nora Compute (§7) and refusing to invent answers for it. This document is
that follow-on.

Brainstormed from three questions the operator posed directly, preserved rather
than pre-answered going in: does Nora need a tool wrapping `drop` at all, or only
through a future `make_artifact()`; should that tool run synchronously or as a
background job; and is the agent that makes artifacts really Nora, or a separate,
neutral instance. All three are answered below, along with several questions that
came up only once the shape was on the table — a governed skill library instead
of raw code-exec, a rendering approach that needed neither MDX nor a component
framework, and an `update_artifact` gap this document surfaces rather than ships
silently missing.

## 1. Motivating shape: deep research + code execution, not a fixed template menu

The operator's own framing, once the shape came into focus: "more like a 'deep
research + create artifact' thing — the deep research is kinda optional though."
Not a narrow, enumerated set of job kinds with known output shapes — genuine
arbitrary code execution, plus the ability to go find information on its own
(web search, maps) rather than relying entirely on what Nora hands it. That
choice is what makes everything downstream in this document non-trivial: a fixed
template menu would need no sandbox, no per-job credential story, no guardrail
section. This does.

## 2. Architecture overview

```
Nora (tag turn, native tool `make_artifact(task: str, artifact_id: str | None)`)
      │  Nora writes the task brief herself — she has already comprehended the
      │  request; Smith never sees the raw transcript (CLAUDE.md's "interpret
      │  once" rule, applied here: Compute executes an already-comprehended
      │  intent, it does not re-parse the conversation)
      ▼
ComputeBackend port (nora/domain/ports.py)  →  LocalDockerBackend (only adapter)
      │  mints a per-job Drop token scoped to this job (or, for an update,
      │  scoped to exactly the one artifact_id being revised), launches one
      │  container from the Smith sandbox image, bind-mounts a workspace dir
      ▼
Smith — a SEPARATE, neutral Strands Agents SDK instance
      │  no persona, no doctrine, no Signal tools, no Honcho access
      │  tools: code-exec, filesystem (workspace), web_search (Tavily/Maps),
      │  a skill library (§6), and its own minimal Drop HTTP client — NOT a
      │  shared import of nora.infra.drop.DropArtifactIngest (different
      │  process, different container; coupling an isolated arbitrary-code
      │  sandbox to Nora's own runtime package would defeat the isolation)
      │
      ├─ researches (optional), writes/runs code, produces one output file
      ├─ writes a short text summary of what it built
      └─ calls Drop's POST /api/artifacts (or PATCH-shaped update) DIRECTLY,
         using its own per-job token — Nora's own credential is never
         involved, and Nora never calls ArtifactIngest herself
      ▼
Job finishes, or hits its timeout/token-budget wall (§5)
      │
      ├─ FAST PATH — make_artifact polls ~15s (tunable default, not an
      │    architectural commitment): job done in time → returns
      │    {url, summary} inline. Summary text passes R-18 like any tool
      │    result. Nora composes her own reply, same turn.
      │
      └─ SLOW PATH — still running past the poll window: make_artifact
           returns "still working" immediately; Nora sends an ordinary
           acknowledgment. Job keeps running under its own separate,
           longer timeout. On completion, a durable outbox row is picked
           up by a poller and delivered via a dedicated turn — same
           shape as the reminders/heartbeat system (nora/app/heartbeat.py,
           nora/app/fire.py): durable row → poll → dedicated turn →
           Deliverer. NORA composes the delivery message from Smith's
           summary; Smith never talks to the group directly.
```

**This supersedes the Drop spec's own §3 diagram**, which showed Nora's tool-call
loop calling `ArtifactIngest` directly. That is no longer accurate. Smith is the
actual caller, on its own per-job credential — a deliberate, reviewed change,
not a silent contradiction of the earlier document.

## 3. Smith: identity and framework

**A separate, neutral agent — not Nora in a different mode.** Nora's persona,
doctrine, Signal tools, and Honcho memory access all stay behind in her own
process. Smith has none of them: its only tools are code-exec, filesystem,
web-search, the skill library, and its Drop client. This keeps a job's own
LLM-written code and research traffic from ever touching Nora's doctrine or
memory, and keeps whatever Smith produces from being mistaken for something
Nora herself said.

**Built on the Strands Agents SDK, same as Nora — not a hand-rolled loop.** The
"neutral" property comes from what tools and prompt are attached, not from which
SDK runs the tool-calling loop. Strands already solves tool-call parsing,
retries, and streaming; a bespoke loop would re-solve that for no benefit and
would be a second way of doing agent loops in a codebase that has been
disciplined about not growing a second version of things it already has one of.

**Smith has its own doctrine file**, `nora/compute/smith/doctrine.md` — governing
prose (mirrors `voice.md`'s role for Nora): when to reach for which skill, the
static-vs-interactive rendering policy (§6), and the CDN allowlist (§8). Persona
and doctrine both live as markdown read at runtime, not hardcoded in Python —
the same convention Nora's own persona already follows.

**The sandbox image is built once, not per job.** `doctrine.md`, every
`SKILL.md` (§6), the vendored `lieflat-charts` templates (§7), and Smith's own
agent code are baked into one Docker image at build/deploy time — the same
image `LocalDockerBackend` launches fresh containers *from* for every job.
A job never rebuilds or installs anything; it runs against whatever the image
already has. Changing a skill or the doctrine is an image rebuild + redeploy,
not a per-job cost.

## 4. `make_artifact`: the native tool

`make_artifact(task: str, artifact_id: str | None = None)` — a plain native
tool in Nora's own registry, same shape as `remind`/`media_fetch`, not an MCP
mount (DEC-2's line: this is a capability Nora owns directly, not a generic
external read-only server).

- `task` is Nora's own distilled brief. She has already comprehended the
  request; Smith receives only what Nora chooses to hand it, never the raw
  conversation transcript. This also keeps unrelated chat content out of a
  sandbox that has open web egress.
- `artifact_id`, when set, means "revise this existing artifact" rather than
  create a new one (§9). `LocalDockerBackend` scopes the minted token to
  exactly that artifact_id in that case — tighter than a create job's token.
- Not terminal. The fast-path inline result (or the slow-path "still working"
  signal) is an ordinary tool result Nora then uses to compose `send_reply`,
  same as `search_gifs` before `send_gif`.

## 5. Guardrails

Named explicitly because this is the most open-ended thing Nora runs — genuine
arbitrary code execution plus its own web egress:

- **Hard wall-clock timeout per job.** Killed regardless of what it's doing.
- **Hard cap on Smith's own model-call/token spend per job**, independent of
  wall-clock time — bounds a job that's technically still running but
  thrashing (retrying, looping on errors).
- **Concurrency limit — max N jobs running at once.** Further requests queue
  or are refused.
- **Web egress is deliberately open for research** (Tavily, Maps, general
  search) — an allowlist here would defeat the "deep research" motivation from
  §1. The allowlist that DOES apply is narrower and different in kind: which
  CDN libraries may be *embedded and executed in an artifact's own HTML* (§8),
  not what Smith itself may fetch while researching.
- **Per-job Drop token**, minted at launch, revoked on container exit. A leaked
  or misused token from inside a sandbox running LLM-written code has a bounded
  blast radius — write access to one job's artifact namespace, not a standing
  credential shared with every other native tool.

Exact numeric defaults (timeout minutes, token/spend ceiling, concurrency N)
are implementation-level, not architectural — flagged in §12 for review before
they're picked, same as the Drop spec left its own numeric defaults (expiry
policy) open in its §8.

## 6. The skill library, not raw code-exec by default

Giving Smith an open-ended code-exec tool and nothing else was the first shape
considered, and rejected: expensive (a full open-ended code-authoring loop for
something as routine as "make a bar chart") and hard to bound (doctrine-as-prose
asking Smith to behave is a weaker guarantee than a function that can only
produce one known shape of output).

**Instead, Smith gets a small set of governed skills — named, tested functions
it calls as tools, each a known recipe:**

| skill | built on | mode |
|---|---|---|
| `render_chart(data, kind)` | `lieflat-charts`, vendored (§7) | Lupi/Basics templates = pure SVG, self-contained. Glance/interactive templates = Chart.js/ECharts via CDN, under the §8 allowlist |
| `render_mermaid(diagram_source)` | `mermaid-cli` (`mmdc`), run server-side inside the sandbox at generation time | static SVG, self-contained by default |
| `render_markdown(text)` | `markdown-it`/`remark-gfm` + Shiki or Pygments for code blocks, both server-side | static HTML+CSS, no client JS |
| `render_map(locations)` | Google Static Maps API | one embedded static image. No equivalent off-the-shelf "skill package" was found for maps — this is a plain API call, not a vendored library, and that's stated rather than invented around |
| `render_html_report(sections)` | composes the above into the shared page-shell (§10) | the default "just produce something readable" skill |

**Each skill is its own `SKILL.md` package**, in the same Agent-Skills format
`lieflat-charts` itself already ships in — `charts/SKILL.md` (vendored, §7),
`mermaid/SKILL.md`, `markdown-report/SKILL.md`, `maps/SKILL.md`, each living
alongside Smith's `doctrine.md` (§3) inside the sandbox image. `doctrine.md` is
the cross-cutting prose (when to reach for which skill, the §8 rendering
policy); each `SKILL.md` is that one skill's own reference, the same division
`lieflat-charts`' own `SKILL.md`/`catalog.md` split already models.

**Raw code-exec stays available as an escape hatch** for jobs that don't fit any
existing skill — keeping the "arbitrary code execution" capability from §1
intact as the exception path, not the default. This is also where §1's residual
risk (§11) stays fully open: a skill's output shape is knowable ahead of time;
the escape hatch's is not.

## 7. `lieflat-charts`: adoption, verified

Investigated directly (`github.com/larashero3-dotcom/lieflat-charts`) rather
than assumed. It is an Agent-Skills-format package (`SKILL.md` + templates +
`catalog.md` + token files), already built for exactly this purpose — "turning
data into polished, interactive HTML charts," consumable by Claude Code, Codex,
and compatible agents. Three visual styles: Lupi (editorial narrative, thin
lines, real units, heavy whitespace), Glance (fast-read dashboard, bold
aggregates), Basics (familiar chart shapes with editorial polish). Delivered
output is a single-file HTML, no build step, double-click-openable — pure-SVG
templates run fully offline; Chart.js/ECharts-based templates need network
unless dependencies are inlined.

**License, verified against the actual text, not assumed:** PolyForm
Noncommercial 1.0.0. Its "Personal Uses" clause explicitly covers "hobby
projects, amateur pursuits... without any anticipated commercial application."
Nora is a private, self-hosted, non-commercial group agent for friends — clean,
permitted use. One real obligation: the license requires a `Required Notice:`
line travel with any distributed copy, and Drop serving pages built from these
templates counts as distribution. `THIRD_PARTY_NOTICES.md` (already present in
the upstream repo) documents Chart.js (MIT), Apache ECharts (Apache-2.0), and
the Inter font (SIL OFL) as the third-party pieces its interactive templates
load via CDN — none of those are PolyForm-licensed themselves, and none block
adoption.

**What ships in this repo:** a vendored copy of the templates and token files
(`mono-tokens.js`, `color-presets.js`) inside the Smith sandbox image, plus a
`THIRD_PARTY_NOTICES.md` in this repo carrying the Required Notice forward.

## 8. Rendering: self-contained by default, an allowlist for interactivity

**No new rendering engine in Drop.** Drop's render route already serves bytes
by `media_type` (Drop spec §3) — `text/html` costs it nothing new. Every skill
in §6 produces output server-side, at generation time, inside Smith's own
sandbox; Drop never runs a renderer.

**MDX was considered and ruled out.** MDX means a JS/React compile step
(`@mdx-js/mdx` or similar) that Drop (Python/Starlette) doesn't have and this
design doesn't want to build — the exact "rendering engine" cost the previous
paragraph avoids. What MDX would have bought — rich structured content (code
blocks, diagrams, tables) beyond plain prose — is what §6's skill library
already provides, via established server-side renderers (`markdown-it`,
Shiki/Pygments, `mermaid-cli`) instead of a JSX component model.

**Self-contained HTML is the default**: inline SVG, inline `data:` URI images,
no external CDN script tags, works offline the moment someone opens the link.

**Interactive artifacts (pan/zoom charts, an interactive Google Map) are
possible but allowlisted, not arbitrary.** This is a real escalation of the
guard-risk carried from the Drop spec (§11) — executable JS in the viewer's
browser, not just unread bytes — so it's bounded structurally rather than left
to doctrine text alone. Smith's `doctrine.md` names the approved set and
nothing else:

- Plotly.js
- Google Maps JavaScript API — **the key used here must be
  referrer-restricted to `drop.nora.example.net`**, never the unrestricted
  server-side key used elsewhere
- Chart.js
- (ECharts, already a `lieflat-charts` dependency for its Glance/interactive
  templates)

Mermaid.js itself could join this allowlist later for a pannable diagram — not
v1; the default stays the static-SVG render from §6.

## 9. `update_artifact`

A gap surfaced mid-design, not part of the original brief: Drop's
`ArtifactIngest` only has `register()` — mint new. A follow-up request ("fix
the Saturday number in that chart") would otherwise only be answerable by
minting a brand-new artifact at a brand-new URL, silently orphaning whatever
link was already shared into the group.

**In scope for v1.** `ArtifactIngest` gains:

```python
async def update(
    self, *, artifact_id: str, data: bytes, title: str, media_type: str,
) -> ArtifactRegistration: ...
```

`make_artifact`'s optional `artifact_id` parameter (§4) is the Nora-side half
of this — when set, `LocalDockerBackend` mints a token scoped to exactly that
one artifact_id (tighter than a create job's token, which only needs to write
a new row) and Smith calls `update` instead of `register`.

**Overwrite in place — no version history in v1.** The update replaces the
artifact's bytes/metadata at the same URL; nothing is kept. Version history
(each update as a new version under the same artifact_id, prior versions still
reachable) is a real feature with its own storage and UX questions — sized
right for a follow-on, not a one-line addition to this document's scope.

## 10. Design system: true-neutral Mono, system fonts, no component framework

Validated with a working spike (§13) before being written down here, not
designed on paper alone.

**Color — true-neutral grayscale for shell chrome, always; one optional accent
per artifact, for content only.** This directly reuses `lieflat-charts`' own
stated rule rather than inventing a parallel one: Mono (true black/white/grey)
is the stable fallback, and a color preset (its celadon/palm/editorial-red
options) is picked only when a given artifact's own data semantics call for
it, never mixed within one delivery. The page-shell itself — headings,
dividers, card borders, background — stays strict neutral (R=G=B at every
step, no hue bias) specifically *because* it has to host whatever accent a
future artifact's own content decides on; a shell with a built-in warm or
teal bias would fight the day a chart's data calls for the red preset instead.
Light and dark themes both defined as token sets, switchable, per the artifact
platform's own three-state convention (explicit choice, or `prefers-color-scheme`
when unstamped).

**Typography — system font stacks, not an embedded custom typeface.**
`ui-serif` (display/headings), `system-ui` (body), `ui-monospace` (code/data,
`font-variant-numeric: tabular-nums` for aligned figures) — CSS Fonts Module 4
generic keywords resolving to each OS's own faces. Chosen over embedding a
custom font because everything else in this design is explicitly
self-contained with zero external/added weight (§8); an embedded font would be
the one exception, adding real bytes to every artifact Smith produces, in
service of pixel-identical cross-device rendering this small private-group
audience doesn't need. The serif/sans/mono role split carries the editorial
hierarchy; the specific face rendering it doesn't need to be fixed.

**No React component library (shadcn/Base UI) for v1.** These are correctly
identified as full component frameworks requiring a JS build step
(`npm install` + a Vite/Next static export), not drop-in-a-script-tag
libraries like Plotly. That build step would commonly exceed the §4 poll
window on its own, pushing nearly every report-type job to the slow path, and
introduces an npm-registry supply-chain surface inside a sandbox that already
runs LLM-written code — the same "too heavy" shape §6 was built to avoid, just
moved from open-ended code generation to open-ended app building.

**Instead: a shared, plain HTML/CSS page-shell** every skill's output renders
into — the actual mechanism that keeps output visually coherent across skills,
rather than each skill guessing at page chrome independently. Covers what a
component library is usually reached for, using what the browser already
provides natively, zero framework, zero build step:

| pattern | mechanism |
|---|---|
| card, timeline, table, stat block | plain HTML + CSS |
| disclosure/accordion | native `<details>`/`<summary>` |
| modal | native `<dialog>` (a few lines of vanilla JS to open/close) |
| select/form controls | native `<select>` and friends |
| carousel | out of scope — CSS `scroll-snap` covers the common case if ever needed, not built for v1 |

**Responsive, mobile-first — the top stated requirement.** Most readers will
open a drop link on a phone; some will use a computer. The page-shell is
built mobile-first (fluid type via `clamp()`, single-column base layout, wide
content — charts, diagrams, tables — scrolling inside its own container so the
page body never scrolls sideways) and this must be verified at real device
widths before shipping, not assumed from the spike alone (§13 states exactly
what was and wasn't verified).

## 11. Guard for job output — risk carried forward, narrowed, not solved

The Drop spec's §7a risk stands: artifact bytes are content no Nora guard has
read, one level past the GIF-attachment risk `design/proposed/15-gif-replies.md`
already accepted for the same reason (small private group, deny-by-default
policy, contained blast radius) — except freeform job output, not a curated
third-party API response.

**What this document adds, without claiming to close the gap:**

- Smith's own short text summary of what it built **does** pass R-18 scanning,
  same as any tool result — the agent's account of its own output is guarded
  text, even though the output itself is not.
- The skill library (§6) narrows the *common* case: a `render_chart` output
  shape is knowable and testable ahead of time in a way raw generated HTML is
  not. The code-exec escape hatch keeps the original, wider risk alive for
  whatever doesn't fit a skill — named here explicitly, not smoothed over.
- Interactive artifacts are a real escalation (executable code in the viewer's
  browser, not just unread bytes) — bounded by the §8 CDN allowlist rather
  than left open to arbitrary `<script>` content.

## 12. Open questions for review

- Exact numeric defaults: sync poll window (this document assumes ~15s as a
  tunable starting point, not a commitment), per-job wall-clock timeout,
  per-job token/spend ceiling, max concurrent jobs N.
- Exact `render_*` skill signatures — sketched by name and purpose in §6, not
  settled function signatures.
- Smith's sandbox image contents in full — Python + matplotlib/plotly,
  Node + `mermaid-cli`, `markdown-it`, Shiki/Pygments, the vendored
  `lieflat-charts` templates, Smith's own agent code and `doctrine.md`. Base
  image and exact dependency pinning is implementation work.
- The job outbox table's exact schema — sized on the reminders/heartbeat
  precedent (§2) but not a reuse of that table; needs its own migration.
- Whether Mermaid.js joins the §8 CDN allowlist later for a pannable diagram —
  explicitly not v1.
- `update_artifact` version history (§9) — explicitly deferred, not designed
  here.
- `THIRD_PARTY_NOTICES.md`'s exact final text once `lieflat-charts` is vendored
  in-tree.
- `make_artifact` needs a `NATIVE_TOOL_BUDGETS` entry, same as every other
  native tool (`design/proposed/15-gif-replies.md`'s own checklist item for
  `send_gif`/`search_gifs`) — not yet assigned a value here.
- Per CLAUDE.md's own convention, this design's new behavioural rules (the
  fast/slow split ending a turn correctly, the per-job token boundary, the
  interactive-JS allowlist) need entries in `design/09-decision-index.md` and
  named tests in `tests/test_acceptance_rules.py::RULE_TESTS` once built —
  not done by this document, which only specifies the rules themselves.

## 13. Validation spike

Before writing this document, the page-shell/design-system section (§10) was
built and published as a working artifact — [Smith
Spike](https://claude.ai/code/artifact/0370dfb3-1846-4bbf-8457-3f5e8e401a01) —
rather than designed on paper alone. It combines one `render_chart`-style chart
(hand-authored SVG standing in for `lieflat-charts`' actual template output),
one `render_mermaid` diagram (this document's own §2 request flow, rendered as
a real diagram), and one `render_markdown` code block, inside the true-neutral
Mono page-shell with light/dark themes and system fonts.

**What it verified:** the three skills' output compose cleanly into one
responsive shell; the shell holds at narrow widths without horizontal
page-scroll; both themes resolve correctly; the neutral-chrome-plus-one-accent
color rule reads as intended once corrected from an initial warm-toned draft.

**What it did not verify**, stated so this isn't overclaimed: the actual
vendored `lieflat-charts` templates (the spike hand-drew a chart in that visual
style, it did not run the real templates), a real server-side `mermaid-cli`
render (the diagram used the Artifact platform's own native Mermaid support,
not the sandboxed render path §6 specifies), or real Drop wiring (no live
`POST /api/artifacts` call was made). Those remain implementation-time
verification, not shown by this spike.
