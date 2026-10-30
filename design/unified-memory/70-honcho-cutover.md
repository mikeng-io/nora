# Honcho cutover: state after the prompt switch, the flip, the hold, step 9

Status: **prep**, 2026-10-19. Grounded in branch `mikeng-io/persona-prompt-switch`
at `54e242700`. The fate of every Honcho call site is already ruled in
[10](10-honcho-audit.md) §4–§5; this node does not re-litigate it. What was
missing is the operational half of [00](00-intent.md) «Sequence» step 9: what
the prompt-switch build changed, the exact flip, what the 14-day hold watches,
and the removal order once it ends. Writing this node executes nothing: the
Honcho client is not deleted here and no live config is touched.

## 1. What the prompt-switch build changed (built)

Under `[persona] render` (with `[persona] enabled` already on — `enabled`
alone is the shadow, `render` is the switch, [45](45-persona-adaptor.md) §5):

| surface | site | under `render` |
|---|---|---|
| tagger `peer_card` block | `nora/engagement/context.py` (`_peer_card`), `nora/app/fire.py` | **not read.** The `<persona>` block renders from the Persona heads instead; the Honcho call early-outs. The code path stays for rollback. |
| tagger `representation` (`peer_history`) | same files | **not read.** Same switch. |
| cognition grounding's self account | `nora/domain/cognition_ground.py` | **Persona.** `<persona of="you">` replaces `self_account`. |
| `character_read` tool | `nora/agent/tools.py` | **Persona** heads for the person/collective; culture fall-through kept typed. |
| debrief fold occasion | `nora/dreamer/` fold wiring | **Persona** account of the room replaces the Honcho read. |
| portal `/memory` | `nora/web/memory.py` | **both.** The caller's Persona account (lines, typed evidence behind each line, open disputes, scoped to the rooms the caller is listed in today) renders BESIDE the Honcho `representation`+`peer_card` entry. |
| memory index | `nora/infra/sql/persona.py`, `nora/infra/sql/memory_index.py` | every `persona_line` write (add, dispute, verdict) projects into the index in the write's own transaction; hidden lines drop their node and the node of the line they supersede. |
| `/health` | `nora/obs/health.py` | gains an `index` block (`status`, `queue`). It is NEVER counted toward `healthy` — a rebuild backlog must not flap the container. `/app/v1/status` deliberately does NOT get it (friend-facing surface; the index is plumbing, and that page's rule is that plumbing stays off it). |

## 2. Still Honcho, on purpose (rollback = a config flip)

Everything 10 §1 lists that §1 above does not name keeps calling Honcho with
the switch ON: the five `feed` sites, `remember` → `conclude`, `about`,
`ask_memory` (`chat`), `search_memory`, `search_memory_workspace`,
`suggest_venue`, `/status` and `/health`'s Honcho reachability (a Honcho
outage still turns `healthy` false), `ensure_workspace` /
`foreign_workspaces` at startup, workspace routing, and both eval seeding
sites. That is the rollback: until step 9 lands, `render = false` plus a
recreate returns every prompt to exactly the pre-switch shape, because no
Honcho path was deleted. Retrieval also reads the switch at the existing
scope boundary: `render = false` excludes `persona_line` from every index
read, including prefetch and tool search, even after an earlier live rebuild.
Rebuild may project shadow rows; projection alone never makes them readable.

## 3. The flip (operator, on `norty`)

> **2026-10-31:** [31 §6](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31) supersedes the fixed sample/parity
> acceptance in the historical 2026-10-21 amendment below. This remains an
> operational preparation document, not proof that its earlier flags match the
> installed deployment. Select the actual mode and validated-copy procedure
> through `nora-deploy`, with operator go-ahead and loaded-config/later-turn
> readback; this amendment authorizes no live operation.

> 2026-10-21 (18 / ADR 0026): **Nora can answer what Honcho could** — the new Views and Honcho's dialectic answer the same frozen question set, judged blind — replaces Character-line fact coverage. The ≥80-pair reply replay stays ([45 §6](45-persona-adaptor.md#6-what-honcho-knew-is-re-derived-and-that-is-measured)). Q1 in [18](18-extraction.md) decides the set is built and frozen before any run, with no editing after the first answer is seen; pass requires blind judgement that the new Views are at least as good as Honcho’s dialectic on ≥80% of questions AND an error rate <5% (answers asserting something their sources do not support). Both must hold; this is not a passed gate or permission to flip. The revised forward sequence in [00](00-intent.md#sequence) puts answering and both gates before the operator's `[memory.index] read` switch; Honcho's feed stops later. The `[persona] render` procedure below is the earlier prompt-switch procedure, superseded in part by [18’s gate](18-extraction.md#the-switch-gate). Views use `memory_search` + pre-fetch over readings, Rollup and entries; Nora’s own turn does the reasoning Honcho’s dialectic agent does, with no new tool.

1. Edit a COPY of the live `config.toml`: `[persona] render = true`.
   `nora config validate` the copy through the running container, `cp` it into
   place (in place, so the bind-mount keeps its inode), recreate — the
   deploy convention's validated-copy procedure. An unvalidated hand edit of
   the live file is never the procedure.
2. `docker compose exec -T nora nora memory rebuild`. Shadow-written
   `persona_line` rows join the index here; the projector does not backfill
   them on its own.
3. Read back, three instruments:
   - `/health`: the `index` block present, `status` ok, `queue` draining to 0.
   - the portal's `/memory` for a listed caller: the Persona block renders.
   - one Langfuse trace of a live tag turn: a `<persona>` block present, no
     `peer_card`/`peer_history` block.
4. If any read-back fails: `render = false`, recreate. Nothing was deleted.

## 4. The hold — 14 days (00 «Sequence» step 9)

The client is deleted only after the authorized switch has held 14 days under
[31 §6](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31). This is operational observation, not statistical
semantic parity. Low/no traffic proves no semantic quality; do not extend the
hold indefinitely solely for volume. During the hold, watch:

- `nora_errors_total{stage="peer_card"}` — flat at zero NEW counts: the path
  is no longer called, so any increment is the switch leaking.
- `nora_peer_card_empty_total` — flat: same reason.
- `/health`'s `index.queue` — drains after each write burst; a queue that
  only grows is a projector defect, not load.
- the projection-failure counter — flat.
- [31 §6](31-build-one-session.md#6-bounded-cutover-acceptance-2026-10-31) owns the bounded pre-switch evidence review.
  The hold measures operational behaviour above; report later real journeys
  and quality limits separately.

## 5. Step 9 — removal, in an order that keeps a rollback until the last step

Each row cites the disposition already ruled in [10](10-honcho-audit.md) §4–§5.
Land as one integrated change per the ponytail rule, after the hold:

> 2026-10-21 (18 / ADR 0026): the historical tool-removal mapping below is amended by [18](18-extraction.md): Honcho’s dialectic maps to the built `memory_search` + pre-fetch surface, with reasoning in Nora’s own turn; no new tool is introduced.

1. **Prompt reads deleted**: the early-out Honcho paths in
   `nora/engagement/context.py` and `nora/app/fire.py` (`peer_card`,
   `representation`), and the `persona_reader is None` branches (10 §4).
2. **Tool reads re-pointed or deleted**: `about` → the person's Persona plus
   the index; `ask_memory` → the index, answered by her own model call;
   `search_memory` / `search_memory_workspace` → the index (their roster and
   scope filters move onto unit/room columns); `suggest_venue` → the index
   (10 §4). Each tool-text change is a `nora-tool` PR with its
   `tool_selection` measurement — this step is several PRs, not one.
3. **Writes deleted**: the five `feed` sites and `remember`'s `conclude`
   (re-pointed: a Record item plus a stimulus; the Persona adaptor writes the
   Identity line, ruled 2026-10-15). `honcho_feed_success` /
   `honcho_feed_failure` stay DECLARED and read zero — the metric-name
   contract is append-only.
4. **Routing and the workspace deleted**: `nora/app/memory_routing.py`,
   `WorkspaceRouter`, `nora/app/memory_carry.py`, `nora/app/memory_detach.py`,
   the per-call budget guard, `ensure_workspace` / `foreign_workspaces` at
   startup. Config: `policy.toml`'s `[units.<u>] workspace` and
   `identity.toml`'s `[workspaces]` go — via the same validated-copy
   procedure, with a backup (DEC-214 unwound; the unit is the only boundary,
   [35](35-persona.md) row 6). The portal's viewer scope moves to the unit in
   the same change.
5. **Health re-pointed**: `/status` and `/health` drop the Honcho check; the
   index's own state (already on `/health`, §1) is the memory health. A
   Honcho outage stops being a Nora health failure.
6. **Eval seeding re-pointed**: `nora/subject/knowledge.py` seeds a Persona
   line; `nora/subject/obtain.py`'s twin-pair binding and
   `nora/subject/memory_workspace.py` go (the eval sandbox is per-database
   already). The 1,218 leftover `nora-eval*` workspaces on the Honcho server
   are deleted once, by hand, after this lands.
7. **The client deleted last**: `nora/infra/honcho/` goes only now, when
   nothing imports it. Honcho's SERVER stays — Nora is not its only user
   (10 §3), and her workspaces' deletion from it is a separate operator act.

## 6. Never carried, never deleted by this

The 21,722 + 1,425 `explicit` conclusions are **dropped as memory** (they have
no citation to a Record row; [35](35-persona.md) §4, operator 2026-10-15) —
nothing migrates them. Dropped from Nora's reads is not deleted from Honcho:
the server keeps them until the operator deletes her workspaces by hand after
step 7.

## The reading that would reopen it

- A hold read-back shows a prompt block Honcho carried that Persona and the
  index do not (the audit's «open» column measured wrong): rollback the flip,
  re-open [10](10-honcho-audit.md) §4's table for that row — the bundle
  becomes a migration of that output, not a deletion.
- The index cannot hold a callsite's load (queue grows unboundedly,
  projection failures climb): the flip's premise fails. Extend the hold,
  fix the projector, delete nothing.
