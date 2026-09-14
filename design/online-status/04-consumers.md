---
type: design
title: "Consumers — the proactive decide turn and the #248 tie-in"
description: "Who calls the tool: proactive decide (the #248 failure family), tag turns later; participation stays no-tool by design."
timestamp: '2026-09-15'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-tool-shape-and-naming, rel: depends_on, provenance: asserted}
---

# Consumers

## Primary consumer — the proactive decide turn

The failure that started this (#248, filed 2026-09-15): the proactive
adaptor read a friends-workspace note that said "Blake sick, wants the
night off" and spoke an answer into the Signal home room — while the group
was mid-game in Discord. The note was born in Zone 42
(`note.established_in`, verified in the DB), the delivery used the home
room, and the decide turn had **no tool that could observe the live state**
contradicting the note.

With `online_now` wired into the proactive turn, the decide pass can:

- read the note's `established_in` and the room roster,
- call `online_now(what="playing", names=roster ∪ established_in_roster)`
  (the decide turn passes that resolved union — the tool's input domain
  for the proactive occasion, 03-guards — so a note born in Zone 42 about
  Blake is checked against Blake's live state even though the decide turn
  stands in the Signal home room), and see that the people the note talks
  about are actually in-game,
- ground its reply in that state — or decline to speak a stale leave
  acceptance at all.

The tool is a **read verb**, so it fits the proactive adaptor's grant
(`AdaptorGrant`, `nora/agent/dreamer.py:362` — every read verb through the
home room, one act verb). DEC-162's D21 boundary holds: this bundle adds a
read verb available to the decide turn; it adds no deliver verb.

Wiring shape (per DEC-155 / `where_been` precedent): the tool's store/port
is threaded at the composition root (`composition.py` builds clients), and
the tool appears in the registry (`tools.py` / `registry.py`) like any
other. The proactive turn gets the tool through the same grant machinery
that already hands it `note_read` — eligibility does not require a new
occasion, and `verify_tool_subsets` must stay green with the tool granted
to proactive but NOT (in v1) to the Dreamer background loops
(`WORKSPACE_SAFE_TOOLS` eligibility is not a grant — same rule as
`where_been`).

## NOT a participation consumer

The participation determination stays **lightweight, no-tool** — the shape
the operator rejected over (the Phase-2 build ran Nora as a full agent loop
when what was wanted was a cheap single-call decision; 2-4k tokens,
transcript + standing only). Adding a tool to that path would be a shape
change, not an extension, and it is explicitly deferred in
`_index.yaml` `scope.deferred`.

If the operator later wants participation to consider live state, the path
is a *separate* decision about that occasion's tool surface — not an
automatic consequence of this bundle.

## Tag turns — reachable on registration

A tag turn can already choose any registered tool; `online_now` will simply
be on the list after the standard tool-surface registration checklist
(registry entry, budgets, READ_ONLY partition, exposed-tool set, doctrine
version, DEC-150 gate, RUF001/RUF002 — all inlined below). No
occasion-specific change is needed for tag turns to use it — the docstring's
trigger conditions (ADR 0007) teach when to reach for it. The operator's
ask 「Blake 而家係咪 online 喺 Steam」in Signal or Discord is answered by a
tag turn calling `online_now(what="online", names=["blake_lindqvist"])`.

## Tool-surface checklist the implementer must run

Applied to `online_now` (checklist follows the class-level procedure that
also produced `where_been`/`remember`; inlined here because the procedure
lives in the operator's agent skills, not in this repo):

- **Tool wiring** — `tools.py` (`build_tool_specs` + callable), `registry.py`.
- **ACT_VERBS** — `read_only=True` → READ_ONLY half, never ACT_VERBS.
- **Budgets table** — `"online_now": N`, sized on NETWORK COST not on
  local-SELECT cost: the Steam arm is a keyed HTTP call with rate limits
  (100 steamids per request), so the budget follows `transit`'s economy —
  one question = one batched call, a second batch is a new question.
- **Exposed-tool set** — both registry exposure tests.
- **ordinary_english** — add `online`/`playing`/`voice` sentence words to the
  narrow catalogue-shape check so ordinary phrases stay honest.
- **Metrics** — `online_now` records via the existing
  `nora_tool_calls_total{tool="online_now", status}` three-state series
  (same `_record` machinery every native tool uses, `tools.py:1322`); no
  new counter, no R-22 `nora_room_lookups_total` (that family stays
  reserved to the R-31 tools). A names-filtered outsider call is therefore
  visible as `nothing_found` in the tool's own status series.
- **Port conformance Fake** — if a new port (Steam client) is added, its
  Fake joins `test_port_conformance`.
- **DEC-150 gate** — doctrine must NOT name the tool; teaching stays at
  behaviour layer ("a claim about whether someone's state matches the note →
  check live state first").
- **RUF001/RUF002** — halfwidth punctuation everywhere; CJK only quoted.
- **Doctrine version** — bump `version:` + PINNED sha when doctrine text
  changes (only if a teaching line actually changes).
- **Zero-wiring check first** — grep the composition root for the Steam
  client / Discord state accessor before adding new wiring; if the Discord
  arm can reuse the listener's existing state object (or
  `current_client()` as `music_tool_bridge.py` does), do not thread a
  second copy.
- **Discord channel resolution** — `channel_name` resolves through the
  client's guild/channel cache, the same path `music_tool_bridge.py` uses;
  do not hardcode channel labels.

## Verification discipline

Per repo convention: worktree → PR → merge, never a direct master commit;
independent adversarial review of the bundle before merge (never
self-review); release fragment written (`release-fragments/unreleased/online-status.json`,
`type: "feat"` for the tool, or "none" for a design-only branch per the
release-fragment gate); both gates run green:
`scripts/check_release_fragments.py --base master` +
`scripts/render_release_notes.py`.
