# Implementation Task: The Relevance Gate (design/dreamer-agent-loop/36 REV 2)

Work in the worktree `/home/norty/workspace/nora-wt-relevance-gate-impl` on
branch `feat/relevance-gate`. Do NOT touch master. Do NOT merge. Do NOT deploy.
Do NOT enable the gate in any committed config.

## Authoritative contract

`design/dreamer-agent-loop/36-relevance-gate.md` **rev 2** is the ONLY
contract. Read it in full first. Its §8 holds ten locked decisions — do not
reopen them. If code evidence contradicts a locked decision, STOP and return
`BLOCKED: <evidence>`; never silently re-decide.

## THE CORRECTION THAT MATTERS

An earlier version of this brief said to RETIRE the participation
implementation. **That instruction was wrong and is withdrawn.**

Participation is NOT retired. The gate stands IN FRONT of it:

- The **gate** takes over the JUDGEMENT ("is this window worth a turn?").
- The **participation occasion** keeps the TURN, and keeps its right to
  silence — see design 36 §7 for the four mechanisms that make that real.

Keep: `nora/app/participation.py` (the trigger — it now calls the gate first),
`FirePath`'s participation occasion, `participation-conduct.md` and the
participation doctrine audience, the slot/cooldown policy, the occasion-gated
recovery, the indicate suppression, the participation metrics, and the 15-case
eval suite. Delete none of it.

## Current branch state

`0b65832b` committed a PARTIAL rename (`participation` -> `relevance_gate` on
`EngageDecision`, `Conversation`, `_ConversationEntry`, and the policy.toml
room key). It does not typecheck — 4 known errors:

    nora/ingest/policy.py:144,145,170   nora/app/dispatch.py:77

Finish that rename. Note the `[participation]` CONFIG BLOCK STAYS
(`settle_seconds`, `cooldown_minutes` belong to the trigger and the turn);
only the per-room policy key and the engage verdict take the new name.

## What to build

Per design 36 §1-§5. Load-bearing points:

1. One structured model call, `nora-gate` combo, reasoning none, no
   tools, no doctrine, no agent loop. Input is transcript + standing ONLY.
2. JSON `{"verdict","relevance_score"}`; unparseable -> `no_go` + counted.
3. Shadow ON: **`go` only** posts to the review channel. `no_go` is never
   delivered, only counted. Nora is never involved.
4. Shadow OFF: `go` -> `FirePath.speak(participation_occasion(...))` with the
   original identity intact and `relevance_score` carried in as context.
   `participation_occasion()` needs a new field for the score.
5. **R-1b**: `CALLERS = {pipeline.py, fire.py}`. The shadow post lives in
   `fire.py` (DEC-155's blessed second call site). The existing `_mirror`
   takes `ctx`/`doctrine`/turn-outcome and CANNOT be reused for a no-turn
   shadow GO — write a leaner sibling in the same module. A third caller
   module is BLOCKED.
6. `[relevance_gate]` config in config.toml AND config.example.toml, both
   `enabled = false`. `nora/config.py` is `extra="forbid"` — see §9.1: this
   must not reach master's config.toml before an image that knows the key.
7. Metrics: `nora_relevance_gate_total{verdict}`,
   `nora_relevance_gate_errors_total`,
   `nora_errors_total{stage="relevance_gate_shadow"}`. Never in a tag,
   heartbeat or participation series. Every declared metric needs a call site.
8. MEASURE the gate's real token cost (§8.7) and write the numbers back into
   design 36 §2.3. Do not ship an assumed `max_tokens`.

## Tests

Gate unit tests: prompt shape (assert doctrine and tools ABSENT), JSON parse
(valid / malformed -> no_go + counted), shadow post shape and GO-only
behaviour, live handoff (identity preserved, score carried), trigger (one call
per closed window).

Full suite must stay green. `uv sync --extra evals` first in this worktree or
pytest dies at collection. Then `make gate` (serial) before claiming done —
note `nora/ingest` is OUTSIDE the gate's mypy scope, so run mypy on changed
files directly too.

Rebase onto master first — the branch is behind.

## Release fragment

Add one under `release-fragments/unreleased/` and validate with
`scripts/check_release_fragments.py --base origin/master`.

## Report

Files changed by section, what was REUSED from participation (not removed),
the measured token numbers, full suite counts, commit hashes.
