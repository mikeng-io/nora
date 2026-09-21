# 05 — Measurement

What would prove this works, what would falsify it, and what is not yet known.

Every number in this bundle is `cl100k_base` (tiktoken), not DeepSeek's
tokenizer. The cached-prefix cross-check in §1 is what makes them trustworthy
anyway; do not restate them as DeepSeek counts.

## 1. The baseline, and how to re-derive it

| | value | source |
|---|---|---|
| input tokens, one `chat` call | 35,473 | Langfuse obs `d7cdd60d4a8a87de`, trace `836eda48d8217234a73587bac33628aa` |
| cached prefix | 30,080 | same, `gen_ai.usage.cache_read_input_tokens` |
| tool schemas, 54 tools | 22,130 | build the registry, `json.dumps` each `tool_spec`, encode |
| doctrine, tag lane | 8,288 | `DoctrineStore.get(applies_to="tag")` |
| tools + doctrine | 30,418 | — |

**The cross-check is the point.** 30,418 against a measured cached prefix of
30,080 is 1.1% — two independent paths to the same number, one from the code
and one from the provider. A decomposition that only added up to the reported
total would be consistent with several wrong splits; this one is not.

Langfuse is at `192.168.1.10:3100`; credentials are in norty's
`nora/.env` under `LANGFUSE_OTLP_HEADERS` (URL-encoded, strip the
`Authorization=Basic ` prefix and unquote).

**Usage figures are a 40-day window** (2026-08-12 → 2026-09-21, 2,000 tool
calls, 574 turns). An earlier draft of this analysis used a 3-day window and
concluded "22 of 54 tools are never called"; over 40 days only four are
(`transit`, `parking_status`, `suggest_venue`, `note_create`), and `route`
(19), `place_info` (18), `social_fetch` (13) and `where_been` (10) are live.
**Never size anything here on a short window** — a rare tool reads as dead.

## 2. The gates, in order

Each must pass before the next is built. A gate that fails stops phase 1; it
does not get its threshold moved.

**2a — Catalogue size.** Render all 54 lines; ≤ **1,400 tokens**, no line >
20. Measured today at **991 / mean 18.4**, so this is a regression guard, not
a hurdle. Cheap, no model calls — run it first.

**2b — Does the provider accept an out-of-array tool?** Send DeepSeek via
OmniRoute a `tool_result` carrying a tool schema for a tool **absent from the
`tools` array**, and see whether it emits a `tool_call` for it.

This is **the highest-risk unknown in the bundle** and it is a single probe.
A negative result forces array mutation (`04` §4), which costs the 30,080-token
cached prefix and changes the economics enough to need re-deciding. Run it
before writing any of §3–§6 of `04`.

**2c — Selection accuracy.** The one that can sink phase 1.

Does Nora pick the right tool from a one-line catalogue as often as from full
schemas? Use PR #334's `tag` entry point — it drives the deployment's own
graph through `nora/subject/obtain(spec)`, so a measured turn carries the real
prefill, every `ContextBlock`, the real registry and `ReplyGuard`. The old
suite could not have answered this: it ran a bare model against doctrine text.

Strands ships `ToolSelectionAccuracyEvaluator` for exactly this shape.

Weight the corpus toward **confusable pairs**, because that is where selection
errors concentrate and where the one-line render is thinnest:
`where_is`/`where_been`, `find_parking`/`parking_status`,
`search_memory`/`search_memory_workspace`, `note_read`/`note_search`,
`route`/`transit`.

**n≥3 per case, and never judge on one.** The 40-case Relevance Gate suite
scored 0–2 errors run-to-run on an unchanged build; one example cannot
separate a fix from noise, and judging by one is how that regression shipped.

**2d — Does she call `load_tools` at all?** The DEC-28 trap: a model read as
refusing to reach for a tool it was never shown. DEC-28's own lesson is that
this is invisible to a log grep — no tool logged its own name, so zero was
consistent with "every turn" and with "never". Measure it on the eval suite
with the counter from `04` §7, not from production logs.

**2e — Cache.** Before/after on `cache_read_input_tokens` across ≥20 real
turns, split by whether the turn called `load_tools`. Expectation: the stable
resident prefix caches at least as well; post-`load_tools` calls may miss. An
expectation is not a result.

## 3. What success looks like

| | today | target |
|---|---|---|
| resident schema tokens | 22,130 | ≤ 3,600 |
| mean input per call | ~35,500 | ≤ 18,000 |
| cold-call TTFT | 4,575 ms | measurably lower |
| tool-selection accuracy | baseline from 2c | **no worse** |
| turns ending without a reply | baseline | **no worse** |

The last two are the ones that matter. A 50% token cut that costs selection
accuracy is a regression, and the token number will look like a success in
every dashboard while it happens.

## 4. After it ships

- **Re-derive the hot set quarterly**, or when a tool's share moves more than
  a point. It is a measured value; `web_search` at 24.9% is a fact about
  2026-08/09, not a constant.
- **Watch `load_tools` for tools already in the hot set** — wasted round trips
  mean the `*` marker is not being read.
- Prometheus is on norty at `127.0.0.1:9190`, ~4d retention, and Nora restarts
  2–3×/day with lazy labelling. **An absent series means "nothing since the
  last restart", never "never happened"** — do not close anything on an empty
  low-frequency counter.

## 5. Open questions — recorded, not planned

- **Does the doctrine belong in the same split?** The same lever points at the
  8,288-token doctrine block, and whitefish-c0 is already moving one rule out
  of doctrine onto a tool result
  (`design/instagram-adoption/06-retention-and-custody.md`). But doctrine is
  authored, operator-owned and governed by `change_policy`; `identity.md`,
  `voice.md` and `constraints.md` are `operator-only`. Separate decision,
  separate owner.
- **Doctrine compression** (identity 973, conduct 3,893) is a separate
  workstream. Two findings from this session belong to it rather than here:
  conduct.md states "empty vs unavailable" in four places (lines 42, 48, 87,
  97) and "read memory before saying you don't know" in three (46, 52, 56);
  and 68% of 40 sampled replies carry an em dash while the doctrine carries 91
  of them and `voice.md` — the file that says how to write — carries one.
- **Is one round trip the right shape?** `load_tools` takes a list, so a turn
  needing three cold tools can fetch them together — but only if the model
  anticipates all three. Whether it does is unmeasured.
- **Lane scoping** (voice lane carrying `generate_image`/`make_artifact`;
  every Signal room carrying the full non-music catalogue) is phase 2 and has
  no bundle of its own yet. Under this split its value shrinks, because an
  unloaded tool costs a catalogue line rather than a schema.

## 6. What would falsify the whole bundle

- 2c shows selection accuracy dropping outside noise on confusable pairs.
- 2b fails **and** array mutation destroys the cache, making mean input worse
  than 35,473 on warm turns.
- Measured cold tools per turn rises toward the 46-tool break-even. Nothing in
  574 turns is near it (max 7), but it is the assumption the arithmetic rests
  on, so it is worth re-checking rather than assuming.

Do not write a falsification that was not run.
