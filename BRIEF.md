# Brief: Nora `remember` tool — Honcho conclusion write lane + eval journeys

## Task
Nora has three persistence concepts (Honcho conclusion feed / note / fact), but **only ONE legitimate write path for "記低" a person-related settled fact: Honcho conclusions** (note = tracked uncertainty, fact = world claims via dream, both reject person descriptions by design). The `remember` tool needs to be a tag-path conclusion write: the agent calls it to durably record a settled fact about a person, which `about`/`ask_memory` then read back — closing the loop that ended with Nora saying "記低咗" while writing nothing anywhere (live incident 2026-08-29: message id 1932 in the `messages` table).

The worktree is `feat/remember-tool` at `/home/norty/workspace/nora-wt-remember`.

## Decisions (CLOSED — do not redesign)

1. **Write target = Honcho conclusions** at `POST /v3/workspaces/{workspace_id}/conclusions` — NOT note, NOT fact. Confirmed by Nora's own `note_create` docstring (nora/agent/tools.py:5271-5276: "a note is TRACKED UNCERTAINTY… not a settled fact and not a description of a person") and the fact-candidates objective (nora/dreamer/objectives/fact-candidates.md:27-29: excludes "anything about a person in this room").
2. **Conclusion pair = (observer=ai_peer, observed=speaker peer)** — the SAME pair `about` reads (`nora/infra/honcho/client.py:528-531`). So a `remember` write is immediately visible to `about`/`ask_memory`. The AI peer id is `self._ai_peer` on HonchoClient; the observed peer id is the tagger's `honcho_peer_id` from the room roster.
3. **Store shape** — follow the existing `feed()` method's best-effort pattern (client.py:455-487): aiohttp POST, `_feed_timeout`-style timeout, return `bool`/`None`-ish benign value on failure, log via `nora.honcho` logger, never raise to the caller. Honcho is "never load-bearing" (ports.py:139-140).
4. **API shape confirmed from live spec** (`http://localhost:8000/openapi.json`, Honcho 3.0.11): `POST /v3/workspaces/{workspace_id}/conclusions` with body `{"conclusions": [{"content": str, "observer_id": str, "observed_id": str, "session_id": str|null}]}`. Content max 65535 chars, min 1. ConclusionCreate requires `content`, `observer_id`, `observed_id`.
5. **New tool name = `remember`**, budget 1, write tool (not read_only, source_tier=None like `note_create`). Docstring must teach the decision tree: "記低 X → 如果 X 係關於一個人嘅 settled fact → remember; 如果係開放問題 → note_create; 如果係 timed → remind".
6. **Doctrine update**: `nora/doctrine/conduct.md` must teach Nora: when asked to remember, call `remember`; only say "記低咗" after the tool returns `ok`; say the truth if it returns `refused`/`unavailable`.
7. **Eval**: add a `remember` journey (seed + paraphrase) to `evals/journeys.py` with the intent graph edge `remember` ↔ (`about`/`note_create`), and wire a canned Honcho conclusion store into `nora/app/eval_support.py::build_eval_tool_registry` so the journey is `_fully_backed`. The eval must test BOTH halves: (a) "記低 X" routes to `remember`; (b) "記唔記得 X" routes to `about` (recall) — the sibling-leakage check forbids the model answering a recall ask by writing.

## Open scope (within this brief)
- Function name/location for the write method on HonchoClient (e.g. `conclude(peer_id, content, target)`).
- How the tool obtains the target peer id (likely via `room.tagger.honcho_peer_id` or roster).
- How `remember` renders the conclusion content (the model authors the text).
- Exact test file names/layout matching repo conventions.

## Invariants (never violate)
1. **Master never touched** — all edits in the worktree; final merge uses no-ff.
2. Honcho writes are **best-effort**: a failure must return a benign `unavailable`-style result, never raise, never block the reply path. (ports.py:139-140, client.py:1-6)
3. `remember` is **fail-closed on identity**: if the room/tagger cannot be resolved (same pattern as `note_create`'s `refused reason=no_tagger`), it must refuse, not write to a guessed peer.
4. The conclusion write is **explicit**: never fall back to writing conclusions to note/fact when Honcho is down — a failed conclusion is `unavailable`, an ERROR is never a PASS (R-27).
5. `search_memory` stays a message-search (session-scoped transcript), NOT a conclusion reader — do not conflate the two. `about`/`ask_memory` remain the conclusion readers.
6. Eval intent graph must include the confusability edges `remember`–`about` and `remember`–`note_create` so a recall-vs-write misroute is caught by NoSiblingLeakage.

## Escalation threshold
- **BLOCK**: invariant violation (writing to master, raising on Honcho failure, refusing to add the doctrine text, conflating search_memory with conclusions), acceptance-criteria contradiction, scope change (e.g. adding a note/fact write path).
- **Execute and record**: function naming, docstring wording, test file placement, whether `session_id` is passed to the conclusions endpoint.

## Artifact targets (read these FIRST, ground every claim)
- `nora/infra/honcho/client.py` — `feed()` best-effort pattern (455-487), `_ai_peer` identity, `_post`/`_url` helpers (305-350), `representation()` pair semantics (513-606).
- `nora/agent/tools.py` — `note_create` refusal pattern (5266-5340), `about` (2090-2100), `search_memory` (2370-2415), `NATIVE_TOOL_BUDGETS` registry position (~360-665), `build_tool_specs` (1747, 5945).
- `nora/domain/ports.py` — MemoryPort / HonchoClient usage precedent (110-145, 780-830).
- `nora/doctrine/conduct.md` — memory honesty rules (100-104), person-question rules (137-152).
- `nora/app/eval_support.py` — `build_eval_tool_registry` (149-171), `BACKED_TOOLS` (33).
- `evals/evaluators.py` — Intent/IntentGraph (74-133), UNMEASURABLE_LIVE (143).
- `evals/journeys.py` — SEED_JOURNEYS + PARAPHRASE_JOURNEYS shape (46-185).
- `docs/eval-sop.md` — Tier 1 (intent, offline) + Tier 2 (live) standards, known limitations.

## Acceptance criteria (each verifiable from artifacts)
1. `git -C /home/norty/workspace/nora-wt-remember diff master --stat` shows edits ONLY under nora/, evals/, tests/, docs/; master untouched in `/home/norty/workspace/nora`.
2. HonchoClient has a best-effort conclusion-write method (name your choice) that POSTs to `/v3/workspaces/{workspace_id}/conclusions` with the confirmed body shape and returns a benign value on failure.
3. `remember` is registered in `NATIVE_TOOL_BUDGETS` with budget 1 and threads through `build_tool_specs` at both signature sites (like `note_create`); its docstring teaches the write-vs-note-vs-remind decision tree.
4. `remember` refuses without a resolvable tagger/peer and returns `unavailable` when Honcho is down, never fabricating a write or falling back to note/fact.
5. tests/contract (or unit) has a test pinning the conclusion-write method's request body (content/observer/observed), the refusal path, and the routing — run with `/home/norty/workspace/nora/.venv/bin/python -m pytest` in the worktree (NO `uv run` — worktree has no `.venv` and `uv run` breaks on strands_evals; use the master venv at `/home/norty/workspace/nora/.venv/bin/python -m pytest`).
6. evals/journeys.py has a `remember` seed journey (e.g. "記低：我屋企有兩隻貓") AND a held-out paraphrase, plus an `about`-recall journey ("記唔記得我屋企有咩動物?"); the intent graph has the required edges.
7. `nora/app/eval_support.py` wires a canned conclusion store (in-memory Honcho double) so the new journeys are `_fully_backed` and `hard_checks_for` treats them as hard.
8. conduct.md has the remember-verb teaching (call `remember`, say "記低咗" only on `ok`, tell the truth on failure).
9. `test_evals_journeys.py` passes offline (tier 1) — intended `remember` path passes, regression path (sibling) fails.
10. Commit is on `feat/remember-tool` with the message convention `feat(media)`-style `feat(agent): …`; no commit touches master.

## Output contract
- Implement in the worktree. Return: one-paragraph summary + the commit SHA + list of files changed + the test command you ran with its pass count + how you verified each acceptance criterion (short, e.g. "AC5: ran X, N passed").
- Do NOT push to any remote. Do NOT merge. The orchestrator merges after review.
