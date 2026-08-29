---
type: design
title: "Capability Drop — Overview & Scope"
description: "Delete the generated capability doctrine block; the tool schemas become the single source of truth for what Nora can do. Why the block exists, why it is now mostly redundant, and what survives it."
tags: [nora, tools, schema, doctrine, capability, prompt]
timestamp: '2026-08-30'
edges: []
---

# Capability Drop — Overview & Scope

## Why

Nora's system prompt carries a *generated* capability section
(`ToolRegistry.capability()` → `kind: capability` doctrine block). It exists
because of DEC-29 (design/12): two hand-written tool catalogues had rotted
(`conduct.md` listed 3 of 7 tools; `_context_header` was stale by four), so the
fix was to render the catalogue from the registry instead of deleting the
catalogue.

That fix closed the *drift* class but kept the *duplication* class. The block
is paid for on every call (`~1.6k tokens` of when-clauses at a 13–17k prompt)
and it mostly repeats what the tool schemas already say. Measured by content
word overlap across all 47 native tools: **85% of each when-clause's content
words already appear in that tool's docstring** — and for `search_memory`,
`weather`, `version`, the music cluster, `lookup_person`, `group_members`, the
overlap is 100%. The schemas are already in the request and state, verbatim,
what each tool does; the capability block's one nominal job — *when to reach
for it in this situation* — is already being done by the docstrings.

Every other agent in the stack has no capability document at all. Hermes (the
operator's, and this repo's own orchestrator) has only the tool schemas; Claude
Code has only its tool definitions. "What can I do" lives in the schema, once.
Nora is the only one carrying a second copy, in prose, generated because two
older hand-written copies rotted.

Miles's framing, verbatim: *"content 唔再要佢，然後佢裏面所有嘅 capability 本身要嘅嘢，
就搬返晒入去個 tools 嘅 schema 入面"* — drop the content, move what it carries
back into the schema, one source of truth.

## What this change is, and is not

**Is:** deleting `ToolRegistry.capability()` and its two render helpers
(`live_only_tools`, `web_tier_tools`), deleting the now-unconsumed `when` field
from `NativeTool`/`ToolSpec`, removing `kind: capability` from the doctrine
tier order, and moving the *two* things the block carried that a schema has no
slot for into `conduct.md` as behavioural rules (see `02-rules-to-conduct.md`).

**Is not:** deleting any `when` information that a tool docstring genuinely
lacks. The three genuinely incremental clauses (most of `recall_lookups`'s
"when you cannot tell whether you checked or guessed it"; `create_event`'s
"genuinely NEW gathering"; `send_reply`'s burst examples) are folded into their
docstrings before the block dies (`01-when-stays-in-schema.md`). It is also not
a rewrite of any tool docstring beyond those merges — the docstrings are
already the schema source and already carry the routing content.

## What is explicitly out of scope

- A future MCP-sourced tool (spec §7/§11) whose schema arrives from outside
  and cannot carry a docstring. When one lands, it sets explicit
  `description`/`schema` on its `ToolSpec` (the fields `adapt()` already
  passes through) — the capability block does not need to be resurrected to
  describe it.
- Re-measuring prompt size after the change. Expected saving is the when-clause
  block (~1.6k tokens on cache miss), partially offset by the two rules moving
  into conduct.md — the exact delta is measured post-implementation, not
  argued here.
- The `skills` plugin tool (`<available_skills>`, DEC-50). It describes itself
  through the plugin's own block and never appeared in capability() — it stays
  exactly as it is.

## Reading order

1. `01-when-stays-in-schema.md` — the redundancy measurement, and which
   when-clause content genuinely survives (merged into docstrings).
2. `02-rules-to-conduct.md` — the two behavioural rules that move to
   conduct.md, and the by-category phrasing that keeps doctrine free of a tool
   catalogue.
3. `03-code-surface.md` — every file and test that touches capability, and the
   exact deletion/change list.

## Open questions — RESOLVED in this revision

1. **Is the when-clause information entirely duplicated?** No — 85% is
   duplicated; ~15% is paraphrase or genuinely extra. The genuinely extra
   pieces are enumerated in `01-when-stays-in-schema.md` and merged in.
2. **Do the closed-world and sourcing rules have a home?** Yes — conduct.md,
   in the same register as its existing judgement rules. `02-rules-to-conduct.md`
   drafts the exact wording.
3. **Does deleting the `when` field break the "constructor raises" gate?**
   The gate exists so a tool cannot be registered without saying when to reach
   for it. With the capability block gone there is no render target for that
   sentence — the gate dies with the field, and `test_tool_registry`'s
   direction pins move to docstring-presence assertions instead.

## Status / Checkpoint

_2026-08-30_ — bundle drafted from a design conversation with miles; nothing
built yet. Supersedes nothing (single-file drafts for this topic never
existed). DEC: **150** (index read at draft time: tail is DEC-149/R-85).
