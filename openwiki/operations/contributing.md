---
type: workflow
title: Contribute and verify changes
description: Worktrees, focused checks, final gates, independent review and the boundary between implementation proof and model acceptance.
tags: [contributing, testing, documentation, openwiki]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:35:43.324Z
sources:
  - id: openwiki-source-f317ee207e1653d2033c81a4
    resource: repo://CONTRIBUTING.md
  - id: openwiki-source-7facb45bd4af011175c1282c
    resource: repo://docs-site/README.md
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
generated: { by: "codex", at: "2026-10-20T00:28:42.613Z" }
---

Nora's source repository remains private. This public wiki explains the implementation; it does not grant repository or production access. Repository contributors use synthetic data and operator-owned review procedures.

## Start with the existing flow

Read the README, architecture map and agent instructions before changing a subsystem. Trace a representative input through callers, persistence, failure handling and delivery. An older design document can explain a choice, but may describe deleted or proposed code.

Every change starts in a separate worktree on a `mikeng-io/<slug>` branch and reaches master through a PR. Preserve unrelated edits in the primary checkout. Use Conventional Commits with a subject that describes the resulting behaviour and a body that records why and what was measured.

## Develop with focused checks

For Python work, install the declared evaluation extra with uv:

```bash
uv sync --frozen --extra evals
uv run pytest -q -n 8 <focused-test-path>
```

Run tests relevant to the edit while iterating. `make test-affected` normally selects from the last commit; it is not an automatic reader of uncommitted edits. Before calling the final PR ready, `make gate` runs full static checks and tests reachable from the branch diff against `origin/master`. Unmapped changes or selector failure run the full suite. Only an explicit `NONE` skips tests.

A regression test should distinguish the repaired behaviour from the old failure. Reverting a fix should make its named test fail. A deterministic double that always returns success cannot establish transport or failure semantics.

## Keep evidence layers distinct

A test can prove a branch takes the intended path with fixed inputs. A model evaluation measures whether the live model chooses correctly. Deployment readback establishes which code and configuration are running. External tracing needs its own persistence receipt.

Read the model's actual input before rewriting an instruction: a judgement about time, identity or whether a matter is still open needs those facts in the rendered evidence. Preserve `FAIL`, `ERROR` and unmeasured outcomes rather than promoting any of them to a pass.

Independent review and a green final gate precede merge. Read the merged state back before claiming it landed. Follow the separate deployment procedure before claiming a change is live.

## Maintain this wiki

OpenWiki owns Claims, source evidence versions, indexes and completion metadata. The host workflow researches and writes the single assigned page, submits its claims, then consumes the next page job. Do not hand-edit managed provenance.

Astro Starlight renders the reviewed Markdown at `/wiki/`; a static build also exports the optional graph. PR builds use no model key. A separate generation workflow is configured to open documentation PRs from master. Weekly inference remains disabled until explicitly enabled after a successful provider trial. MiniMax M3.1 Flash is the configured default; its native multi-turn compatibility has not yet been accepted.

The public reader and search work without model inference. A public ask-agent service and embeddings are future work, not prerequisites for reading the wiki.

Return to [the topic map](../quickstart.md) or [operating configuration](configuration.md).
