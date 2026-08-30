---
kind: design
title: "Nora — Recall and Threading"
description: "Two related gaps: she can only recall the tagger, and she cannot see that one message replies to another. A prefilled room-scoped recall block sourced from Honcho's representation endpoint, and a one-column parent pointer."
tags: [nora, design, honcho, memory, threading, prefill]
timestamp: '2026-08-06'
status: proposed
edges:
  - {dst: 10-identity-and-policy-schema.md, rel: extends, provenance: asserted}
  - {dst: 09-decision-index.md, rel: extends, provenance: asserted}
---

# Recall and Threading

Two failures with one shape: Nora is handed a flat list of strings and asked to
reason about a conversation. She cannot look up anyone but the person who tagged
her, and she cannot see that one line answers another.

**This document builds on the roster design in the same session** (`RosterStore`,
`PlatformAdapter.rosters()`, R-19 prompt-scope enforcement, `status="unavailable"`,
unmapped members rendered by display name). Every dependency on it is flagged
inline. Problem 2 depends on none of it.

## Recommendations

**Problem 1 — recall.** Prefill a room-scoped `<recall>` block built from Honcho's
`POST /peers/{id}/representation` (0.19 s, no LLM inside, question-conditioned via
`search_query`) for every mapped person in the tagged room, and **delete
`read_peer_card`** — do not add a lookup tool.

**Problem 2 — threading.** Add one nullable column `quoted_ts` carrying
`dataMessage.quote.id`, resolve it to a parent row in memory at prefill time, and
render it as a one-hop inline arrow `[Tyler ↳ Grant]: 你屋企` — no graph, no
second column, no `raw_json` parsing on read.

---

# Part 1 — Recall

## 1.1 What is actually broken, measured

`nora/engagement/context.py:64` fetches exactly one peer card, for the tagger.
That is the stated problem. It is not the worst one.

Probed against the live instance (Honcho 3.0.11, `http://127.0.0.1:8000`,
workspace `nora`) on 2026-08-06:

| Call | Latency | Result |
|---|---|---|
| `GET /peers/miles_novak/card` | 5 ms | `{"peer_card": null}` |
| `GET /peers/miles/card` | 5 ms | `{"peer_card": null}` — **and `miles` has 8 derived conclusions** |
| `POST /peers/miles/representation` (`search_query`) | **0.19 s** | 8 dated observations, useful |
| `POST /peers/miles_novak/search` | 0.18–0.27 s | raw fed messages |
| `POST /peers/miles_novak/chat` (`reasoning_level:"minimal"`) | **6.9 s / 8.3 s** (two runs) | a genuinely good answer |
| `POST /peers/miles_novak/chat` with `target:"nora"` | 15 ms | `404 — Peer nora not found in workspace nora` |

Three findings, in order of severity.

**(a) `peer_card` is still dead.** Commit `4d35244` fixed the client
(`nora/infra/honcho/client.py:167-189` — the field is `peer_card` and it is a
list). The endpoint still answers `null`, *including* for the one peer that has
conclusions. The recall channel has never carried a single line in production.
This is bug class #6 in this repo's own catalogue — a shape nobody checked
against the live service — and the fix for it happens to be the same endpoint
this design needs anyway.

**(b) Honcho's deriver is stalled.** `GET /queue/status`: `total 28, completed 12,
in_progress 0, pending 16`. The 16 pending units belong to the session feeding
`miles_novak`. Every conclusion in the workspace carries `observer_id: "miles"` /
`session_id: "nora-miles"` — the *retired* person_id. The `miles` → `miles_novak` rename
orphaned all derived memory, and nothing has been derived since.

**(c) There is nothing to recall about anyone else.** `data/messages.db` holds
20 rows, one conversation (`Ridgeline`), `person_id` ∈ {`miles_novak`, NULL}. Tyler,
Grant, Blake, Aaron and Avery are in `identity.toml` and have **no Honcho peer
at all** (`POST /peers/list` → `85212345678`, `85200000000`, `miles`, `miles_novak`).

> **This is why "build FIRST" below starts with an ops step, not a code step.**
> Any recall feature shipped today is a no-op that looks like a feature. That is
> precisely the failure mode `--receive-mode=on-start` produced for 61 days.

## 1.2 Tool or prefill — prefill, and delete the tool that exists

The operator asked directly. **Prefill.** Four reasons, in descending strength.

**A tool cannot fix this failure, by construction.** The failure being repaired is
*the model answering confidently without asking*. A tool requires the model to
decide to ask. The live evidence says it will not: `nora_tool_scope_violations_total{tool="send_reply"} = 2.0`
against `nora_replies_total{status="sent"} = 2.0` — the model called `send_reply`
on turn one of **both** live tags, and got the `conversation_id` wrong on both.
`tool_choice="required"` (`nora/agent/factory.py:44-72`, load-bearing per DEC-21)
makes `send_reply` the attractor on the first turn, and `ReplyGuard._before_model`
(`nora/agent/guards.py:256-273`) cancels the turn the moment delivery lands. An
optional lookup tool sits in a window the model has never once entered.
`read_peer_card` has existed for the whole deployment and has never been called.

**The round trip a tool costs is a model round trip, not an HTTP one.**
`reasoning_effort="none"` buys 4.7 s (`config.toml:34-36`, measured). A tool call
plus a second inference roughly doubles the tag. `representation` costs 0.19 s and
fans out concurrently across a six-person room for the same 0.19 s wall clock.

**"Prefill cannot be conditioned on the question" is false here.**
`PeerRepresentationGet` takes `search_query` — verified against the live OpenAPI —
which curates the returned conclusions around a semantic query. Nora already holds
the question: it is `ctx.message.body`. The prefill is question-conditioned; it is
merely not *model-directed*. That was the only real advantage a tool had.

**Prefill is scoped by construction, and strictly more so than DEC-21's idiom.**
DEC-21 holds R-3 by construction — the model's argument is inert and mismatches are
counted. A prefill has *no argument to be inert*. There is no enumeration primitive
to abuse because there is no primitive.

### Delete `read_peer_card`

`nora/agent/tools.py:109-125` is the cautionary precedent the brief names, and its
own comment (`:98-108`) documents the gap: hardcoded `"signal"`, model-supplied
`sender_id`, no check that the person is in this conversation. The honest move is
not to add a second, better-scoped lookup tool beside it — it is to remove the
unscoped one. It is an address-book read surface that has never been called, whose
backing endpoint returns `null`, and whose docstring instructs the model to use it
"if the recall block is empty" — which it always is.

Removing it drops `NATIVE_TOOL_BUDGETS` (`tools.py:27-31`) to two entries. DEC-10's
machinery makes that a one-line change; R-7's default-deny then vetoes it if the
model invents it. `_context_header` (`nora/agent/prefill.py:31-40`) loses its
`read_peer_card` line — which A11/R-15 require, since the header must state what is
present, and the recall block will now genuinely be present.

### The hybrid, and why not

The brief offers "cheap facts prefilled, expensive lookup as a tool". The expensive
lookup is `chat`, at 6.9–8.3 s against a ~5 s budget. There is no hybrid to build:
the cheap half is the whole of what fits, and the expensive half belongs off the tag
path entirely (see §1.6).

## 1.3 Which Honcho call

**`representation`.** `POST /v3/workspaces/{ws}/peers/{id}/representation`, fields
verified live: `session_id`, `target`, `search_query`, `search_top_k`,
`search_max_distance`, `include_most_frequent`, `max_conclusions` (default 25, max
100). Response is `{"representation": "<string>"}` — pre-rendered prose, dated,
already grouped under `## Explicit Observations`.

- **`card`** — static, and empirically `null` even for a peer with conclusions. Not
  a source. Keep the client method; stop depending on it.
- **`chat`** — an LLM call inside Honcho. 6.9 s and 8.3 s at `reasoning_level:"minimal"`,
  the cheapest level of five. Disqualified from the synchronous tag path on latency
  alone, twice measured.
- **`peers/{id}/search`** — 0.18–0.27 s, returns raw fed `Message` rows. Useful, but
  it returns *transcript*, and Nora already has 40 rows of transcript. Rejected as
  redundant with `read_buffer`.
- **`target=`** — theory-of-mind, and the right shape for "what does Tyler think of
  Grant?". **Not in v1.** Choosing between `target=None` and `target=X` requires
  classifying the question's shape, which needs an LLM, which is the thing being
  avoided. It also 404s unless the target peer exists — `nora` is not a peer today
  (see open question 4).

Call shape per person:

```
POST /peers/{person_id}/representation
{ "search_query": <tag message body, truncated>, "max_conclusions": 5, "search_top_k": 5 }
```

`session_id` is deliberately omitted: sessions are per-peer (`nora-<peer_id>`,
`client.py:144`), so scoping to one would return a subset of what the unscoped call
already returns for that same peer.

## 1.4 Latency and failure

Recall is assembled inside `TranscriptInjection.assemble`
(`nora/engagement/context.py:50-74`), which runs *before* the model call — so every
millisecond is on the tag budget. Therefore:

- **One deadline for the whole fan-out**, not per call. `asyncio.gather` under a
  single `asyncio.timeout` (propose `memory.recall_timeout_seconds = 0.5`, config,
  per D5/DEC-8). Whatever landed is rendered; whatever did not is simply absent.
- **Never fail the tag.** Same contract `peer_card` already has (`client.py:167`,
  "[] on any failure"): a slow or dead Honcho yields a thinner prompt, never a
  missing reply. The `except Exception` in `assemble` (`context.py:58-60`) is the
  existing precedent.
- **Count the difference between empty and absent.** This is load-bearing, not
  hygiene. `nora_recall_total{status}` with `status ∈ {ok, empty, timeout, error}`,
  plus `nora_recall_people` (histogram: how many people were asked about). A recall
  block that is silently always empty is exactly `peer_card`'s failure, and a
  counter that cannot distinguish "asked, nothing known" from "never asked" would
  have hidden it for another two months. Alert on `ok == 0` while `people > 0`.

## 1.5 Privacy

**Alias resolution as a lookup target is safe only if the target set is bounded
before any alias is consulted.** design/10 §1.3 says aliases are comprehension-only
and never resolve a SENDER; here they would resolve a lookup TARGET, which is a
different operation with a different exposure.

The mechanism that makes it safe: **the alias index is built per-room, from the
roster, never from the global `Persons.index`** (`nora/persons.py:47`). Nora does
not resolve a name and then filter — she resolves *within* a set that already
contains only people in this conversation. Resolve-then-filter leaks membership
through behaviour differences; resolve-within-scope cannot.

> **Depends on the earlier design.** The room membership set comes from
> `RosterStore` / `PlatformAdapter.rosters()` and is exactly R-19's prompt scope.
> Recall follows the roster by construction — same set, same call site. Without
> that design, this reduces to the tagger only (§1.7 step 2), which is still a
> strict improvement and needs nothing new.

What stops *"tell me everything about 肥仔"* from becoming an address-book dump:

1. **No enumeration primitive exists.** With `read_peer_card` deleted, there is no
   call the model can make to reach a person who is not already in its prompt.
2. **The prompt only ever names people in this room.** The commit that removed the
   roster (`nora/engagement/pipeline.py:168-176`) records the live incident: asked
   who was in the group, she listed five people who had never been members. That is
   R-19's whole reason for existing, and recall inherits it.
3. **R-18 backstops identifiers.** `nora.agent.egress.scan_reply` fails closed on
   E.164, UUID and key-shaped strings — and Honcho conclusions can and do contain
   those (a fed exchange carries whatever was said). Worth an explicit test: a
   conclusion containing a phone number must not be relayable. The conclusions
   themselves are filtered with `scan_identifiers`. (The reply predicate once
   ALSO refused model names; that veto was disabled 2026-08-31 by operator
   decision — `social_fetch` relays third-party content that names models, and
   the whole-reply veto cost real answers. See `nora/agent/egress.py`.)

**What is NOT covered, honestly.** R-18 does not block names or facts. Nothing
mechanical stops Nora repeating *"Tyler told you he was broke"* into the room Tyler
is standing in. That is a policy question, not a mechanism one — open question 2.
The available mitigations are a `constraints.md` line (a request to a model), a low
`max_conclusions` (less to dump), and an adversarial eval item. None of them is a
guard. Say so rather than implying otherwise.

## 1.6 What should NOT be built

- **~~No lookup tool.~~ RETRACTED 2026-08-06 — the settling measurement ran
  and §1.2 lost.** `recall(person)`, `lookup_person(name)` and
  `group_members()` shipped as TOOLS in commit `f630d8b` (DEC-27, R-21, R-22),
  which is precisely what this line forbade. The disagreement was recorded as
  empirical rather than argued, and it has now been settled empirically.

  §1.2's case rested on one measured claim: that `read_peer_card` "has existed
  for the whole deployment and has never been called", because
  `tool_choice="required"` makes `send_reply` the turn-one attractor and
  `ReplyGuard` cancels on delivery — so an optional lookup tool sits in a
  window the model never enters. **That claim is dead.** Measured against
  `opencode-zen/deepseek-v4-flash` with the real schemas and the real
  assembled doctrine, 「邊個係阿豹妹？」:

      turn one    lookup_person 6/6      (doctrine only, and full prefill: 6/6 each)
      turn two    recall        6/6      (after lookup_person returns a person)

  The model does not merely enter that window, it enters it *first*: `recall`
  and `lookup_person` beat `send_reply` on every run of a person-question, and
  under the pre-audit doctrine as well as the current one. What changed since
  the original measurement is what §1.6 already guessed — `reasoning_effort`
  moved to `"low"`, and `_context_header` names every tool in prose.

  So the tools are not dead code, prefill is no longer the only workable
  answer, and §1.2 above should be read as history. Its *other* arguments
  survive on their own merits and are unaffected: `chat` is still too slow for
  the tag path, `representation` is still the right endpoint, and
  `read_peer_card` should still be deleted — see the note under §1.2's «Delete
  `read_peer_card`», which the tools phase did not act on.

  **What the tools do NOT fix, and what looked like a tool-selection failure
  but was not.** A live tag asked 「邊個係阿豹妹？」 and Nora answered
  「呢個group冇人叫阿豹妹喎」 — nobody here is called that — which is a near
  verbatim rendering of `_miss()`'s own `not_found` note. She called the tool
  and relayed its answer faithfully. The answer was correct: `listGroups`
  reports the `Ridgeline` group as three accounts (two of Miles's, one of Nora's),
  Tyler is in `identity.toml` but not in that group, and R-21 requires exactly
  this — a person outside the room is indistinguishable from one who does not
  exist. The gap is in `identity.toml`/the roster, not in the model's
  willingness to look.
- **No `chat` on the tag path.** Measured 6.9–8.3 s.
- **No `target=` theory-of-mind in v1.** §1.3.
- **No recall for unmapped members.** They have no peer. They render by display
  name with no recall block, per the operator's decision, and that asymmetry is
  itself informative — it shows the operator who is missing from `identity.toml`.
- **No caching layer.** `representation` is 0.19 s and the answer changes as people
  talk; a TTL cache buys a fifth of a second and introduces staleness that is
  invisible under R-5.

## 1.7 Change list — Part 1

| File | Change |
|---|---|
| `nora/infra/honcho/client.py` (201 L) | `+ async def representation(*, peer_id, search_query, max_conclusions=5) -> list[str]` — split the returned prose on newlines, strip `[date]` prefixes and `-*` bullets exactly as `peer_card` does (`:189`). ~25 lines, lands ≈226. |
| `nora/domain/ports.py` (288 L, cap 300) | `MemoryStore` gains `async def representation(...) -> list[str]`. Two lines. **Tight** — if any other port change lands in the same phase, split `MemoryStore` out first. |
| `nora/engagement/recall.py` (**new**) | One responsibility: given a roster and a query, return `Mapping[person_id, tuple[str, ...]]` under one deadline, counting outcomes. ~70 lines. New module rather than growing `context.py`, so the deadline/fan-out logic is unit-testable without an `RecordStore`. |
| `nora/engagement/context.py` (74 L) | Call `recall.gather(...)`; put the result on `TagContext`. ~8 lines. |
| `nora/domain/models.py` (146 L) | `TagContext.recall: Mapping[str, tuple[str, ...]] = MappingProxyType({})`. stdlib only. |
| `nora/agent/prefill.py` (131 L) | `_recall_block(ctx, roster)` — see §1.8. Drop the `read_peer_card` line from `_context_header` (`:31-40`). ~20 lines. |
| `nora/agent/tools.py` (176 L) | **Delete** `read_peer_card` (`:109-125`) and its `NATIVE_TOOL_BUDGETS` entry (`:29`) and `ToolSpec` (`:145-148`). `persons`/`honcho` become unused parameters in `build_tool_specs` — remove them, and from `ToolRegistry.specs`/`.build` (`nora/agent/registry.py`) and the pipeline call site (`nora/engagement/pipeline.py:158-163`). |
| `nora/config.py` (268 L) | `memory.recall_timeout_seconds`, `memory.max_conclusions`. |
| `nora/obs/metric_specs.py` (208 L) | `nora_recall_total{status}`, `nora_recall_people`. |
| `nora/testing/fakes.py` (281 L, near cap) | `FakeMemoryStore.representation`. |
| `design/09-decision-index.md` | DEC-22; R-19b. |
| `tests/test_acceptance_rules.py::RULE_TESTS` | `"R-19b": "test_recall_is_never_fetched_for_someone_outside_the_room"`. |
| `evals/adversarial/` | one item for "tell me everything about \<alias\>". |

## 1.8 Prompt syntax

```
<recall>
What you durably remember about the people here. Not something they said just now.
[Tyler] repeatedly asks whether you can see the time; prefers Cantonese.
[Grant] organises the dinner plans; lives in Sha Tin.
</recall>
```

One line per person, name in the same `[Name]` vocabulary the transcript uses so no
new symbol is introduced. People with no recall are omitted entirely rather than
rendered empty — an empty entry teaches the model that the block is unreliable.
Escaped and untrusted-labelled per R-13, same treatment as `_transcript_block`
(`prefill.py:43-52`).

---

# Part 2 — Threading

## 2.1 The gap, and two bugs found while verifying it

`_quoted` (`nora/platform/signal/parser.py:43-63`) captures
`(author_id, author_name, text)` onto `NormalizedMessage.quote`
(`nora/domain/models.py:38`), and `build_tag_message` (`nora/agent/prefill.py:118-131`)
renders `<replying_to>` — for the tagging message only. `messages` has no quote
column, `TranscriptRow` (`nora/domain/models.py:75-91`) has no quote field, and
`_transcript_block` (`prefill.py:43-52`) renders a flat `[who]: body`. Confirmed.

Two things surfaced while checking the live envelope (`data/messages.db` row 21,
the only stored row carrying a quote):

**Bug A — `<replying_to author="">`.** signal-cli's quote payload carries `id`,
`author`, `authorNumber`, `authorUuid`, `text`, `attachments`. There is **no
`authorName`**. `_quoted`'s `q.get("authorName") or ""` therefore yields `""` on
every real message, and `build_tag_message:124` renders an empty author attribute.
The one feature that does thread — the tag message — has been rendering
*"replying to nobody"* since it shipped.

**Bug B — Nora's own rows can never be joined, and this is the design constraint.**

```
quote.id                  1785950108291     ← what row 21 replies to
messages.ts for that row  1785950108283     ← Nora's reply, row 20
                          ------------- 8 ms apart
```

`Deliverer` stamps `int(time.time() * 1000)` locally (`nora/agent/guards.py:116`).
Signal assigns its own sent-timestamp, and that is what a quote points at.
`SignalSender.send` (`nora/platform/signal/send.py:80-85`) reads the JSON-RPC
response only to check for `error` and discards the body.

A reply *to Nora* is the most common reply in a group where Nora is tagged. Any
threading built on exact-`ts` matching resolves everyone except her.

## 2.2 Where threading lives — one column

**Rejected: parse `raw_json` on read.** `RecordStore.recent` already returns
`raw_json` (`nora/infra/sql/audit.py:104`), so it looks free. It is not:

- **It cannot work for Nora's own rows at all.** Her `raw_json` is
  `{"tag_id", "source", "doctrine_sha256"}` (`guards.py:118-119`) — there is no
  envelope to parse. Same rows as Bug B, decisive twice over.
- It forces `dataMessage.quote` knowledge into `nora/engagement` or
  `nora/agent/prefill.py`, which are platform-neutral. `nora/domain` is stdlib-only
  and `TranscriptRow` is the contract; a platform-specific shape must not be
  re-derived above the adapter.
- 40 `json.loads` per tag, on the reply path, forever.

**Rejected: derived in-memory index.** That is the *rendering* step (§2.4), not an
alternative to storing the pointer. There is nothing to index without a column.

**Accepted: one nullable `quoted_ts INTEGER`.**

**One column, not two.** The brief offers "quoted author + quoted ts/id". The author
is *derivable* from the parent row once found — and the parent row's `sender_id` /
`person_id` is authoritative, whereas `quote.author` is supplied by the person
constructing the quote. Storing both creates two sources of truth that can disagree,
and the untrustworthy one is the one that would be rendered. Store the pointer;
derive the attribution.

Mechanics, following `0002_person_id.py` exactly:

- `nora/infra/sql/schema.py`: `sa.Column("quoted_ts", sa.Integer)` declared **LAST**,
  after `person_id` (`:61`), before the `CheckConstraint` — `ALTER TABLE ADD COLUMN`
  appends, and `:56-60` documents that a mid-table declaration shifts `ts`'s column
  index and breaks `test_indexes_match_production_exactly`. Update that comment's
  "Any future column goes here too".
- `0003_quoted_ts.py`: plain `op.add_column`. **No `batch_alter_table`** — batch mode
  rebuilds from reflection and reflection cannot see `idx_messages_conv_ts`'s
  `ts DESC`, so it would silently downgrade the index to ASC (`0002:5-10`).
- **No index on it.** Same reasoning as `person_id` (`0002:11-12`): tiny cardinality,
  and the one index that matters must not be disturbed.
- Carry `0002`'s NOTE forward: run the migration before `uv run pytest -q`, or
  `tests/infra/test_schema_parity.py` fails with a column-set mismatch that reads
  like a schema bug.

**Nothing touches the ingest hot path.** `quoted_ts` is a value the parser already
extracts, written in the **same INSERT** (`nora/infra/sql/audit.py:65-77`) — exactly
what DEC-15 did for `person_id`, and for the same reason: `audit.py:1-10` documents
that WAL permits one writer and this path drops the message on contention.

## 2.3 Fixing Bug B — free, on a write that already exists

`SendResult` (`nora/domain/ports.py:147-152`) gains `timestamp: int | None = None`.
`SignalSender.send` reads it off the JSON-RPC result instead of discarding the body.
`Deliverer` then corrects the row's `ts` **inside the `set_delivery_status` call it
already makes** (`guards.py:139-143` → `nora/infra/sql/audit.py:129-141`) — rename to
`set_delivery_outcome(row_id, status, ts=None)` or add the parameter. **No new write,
so the single-writer constraint is untouched.**

A9's record-first ordering is preserved: the row is still written before the send,
and the correction lands on the existing post-send update. An 8 ms adjustment cannot
reorder `recent`'s `ts DESC, id DESC` against human-typed messages.

*Rejected:* tolerance-window matching (±2 s). A heuristic that silently mis-threads
under burst, when the exact value is available for free.

## 2.4 Rendering — one arrow, one hop

deepseek-v4-flash, ~5 s. Notation must be cheap, positionally obvious, and use
symbols already in the line vocabulary.

```
<group_transcript untrusted="true">
[Grant]: 邊度食飯
[Tyler ↳ Grant]: 你屋企
[Miles]: 好
</group_transcript>
```

Plus one sentence in `_context_header` (`prefill.py:31-40`):
`[A ↳ B] means A was replying to B's message.`

Three tokens, and only threaded lines pay. The name after `↳` is **the same string
used as a line label** — `row.sender_name or "unknown"`, or `display_name` when
`is_nora` (`prefill.py:45`) — so the model joins them by literal match with nothing
to infer. That also keeps it consistent with the operator's decision that unmapped
members render by display name.

Rejected alternatives:
- **Line ids (`#12` / `re:#12`)** — forces pointer-chasing *and* invites the model to
  emit `#12` in its reply. Two failure modes for one gain.
- **Nested XML (`<message><reply_to>…</reply_to></message>`)** — token-expensive
  across 40 rows; small models lose structure in nesting.
- **Indentation** — ambiguous with multi-line bodies, and Signal bodies are
  routinely multi-line.

**Ambiguity accepted:** if Grant spoke twice, `↳ Grant` does not say which. The
model needs to know two turns are *connected*, not which row. If evals show
otherwise, the escape hatch is appending the parent's first ~6 characters
(`↳ Grant:"邊度食"`). Ship without; measure.

### `<replying_to>` and a forged-quote hole

`build_tag_message` (`prefill.py:121-126`) renders `ctx.message.quote`'s **text**,
attributed to a named author, inside `<tag_message>`. That text comes from the
*quoting* sender's envelope and is verified against nothing. Alice can construct a
quote asserting Bob said anything at all. R-13 escapes it; escaping does not make a
forged attribution safe.

**Rule: quoted text shown to the model is the resolved parent row's text, or
nothing.** Resolve `quoted_ts` → parent row, render that row's body and that row's
resolved author. If it does not resolve, render the arrow with the author only, or
omit the block. Never the envelope's copy. This also fixes Bug A as a side effect —
the author comes from a row Nora stored, not from an absent `authorName`.

## 2.5 How far back it resolves

`context_window = 40` (`config.toml:47`).

1. **In window** — build `{ts: row}` from the rows already in hand, O(n), free. Render
   `↳ Name`, and for `<replying_to>` the parent's body.
2. **Outside the window** — one bounded lookup:
   `RecordStore.by_timestamps(conversation_id, ts_values, limit=10)`, covered exactly
   by `idx_messages_conv_ts` (`conversation_id`, `ts DESC`) as an index seek. One
   extra SELECT per tag, on the reply path, against a table capped at 500 rows per
   conversation (`config.toml:46`).
   **An out-of-window parent contributes an ATTRIBUTION, never TEXT.** The window is
   a context budget; threading must not be a hole through it.
3. **Unresolvable** (pruned, pre-migration, or a foreign conversation) — render a
   plain `[who]: body` with no arrow. **Never `↳ ?`.** Teaching the model that a
   symbol can mean "unknown" invites it to emit one. Silent degradation, consistent
   with R-5's temperament.

## 2.6 Interaction with `read_buffer`

Yes, and it is a trap. `read_buffer` (`nora/agent/tools.py:52-96`) returns
`{"sender", "body", "ts"}` from the same `store.recent`. If the prefilled transcript
teaches `[A ↳ B]` and `read_buffer` then returns un-threaded rows, the older messages
look *structurally different* — the model reads a missing `↳` as "not a reply".

**Add threading to `read_buffer`'s output as a `"reply_to": "<name>"` key** — JSON,
not the arrow, because a tool returns structured data and the arrow is a prompt-syntax
concern. Resolution happens **only within the rows that call already returns** (up to
200, a superset of the 40-row window): no extra query from inside a tool. Unresolved
→ key omitted.

Nothing about R-3/DEC-21 changes: `read_buffer` still reads `bound_conversation_id`
(`tools.py:50, 84`), the model's argument stays inert, and mismatches stay counted.
The tool gains a field, not a parameter.

## 2.7 Parent pointer, not a graph — what NOT to build

A full graph is not warranted. Do **not** build:

- **A `threads` table, thread ids, or root-of-thread computation.** Signal has no
  thread concept; it has a quote. Inventing one means maintaining derived state that
  can disagree with the rows.
- **Transitive chains (`A ↳ B ↳ C`).** One hop only. Two hops doubles token cost and
  the model cannot use it inside 5 s.
- **Reaction edges.** `_body_parts` (`parser.py:66-88`) already documents why a
  reaction is not a message. Making it an edge reintroduces exactly what that comment
  rejects.
- **Mention edges.** `NormalizedMessage.mentions` (`models.py:34`) is tempting as a
  second edge type. It is already covered — the mention renders as U+FFFC in `body`
  and the roster names the person. An edge type for it is a graph pretending to be a
  feature.
- **Forward links** ("replied to by…"). Derivable, unused, doubles render cost.
- **Backfilling `quoted_ts` from `raw_json`.** 20 rows exist; one of them has a quote.
  Start clean (open question 6).

## 2.8 Change list — Part 2

| File | Change |
|---|---|
| `nora/domain/models.py` (146 L) | `@dataclass(frozen=True) class Quote: author_id: str; text: str; quoted_ts: int`. `NormalizedMessage.quote: Quote \| None`. **Drop `author_name`** — verified always `""` on the live wire; a field that is provably always empty is how the `peer_card` `content` bug happened. `TranscriptRow.quoted_ts: int \| None = None`. |
| `nora/platform/signal/parser.py:43-63` | `_quoted` returns `Quote(author_id, text, quoted_ts=int(q["id"]))`. Return `None` when `id` is absent — a quote with no pointer is not threadable. |
| `nora/infra/sql/schema.py:61` | `quoted_ts` column, declared LAST. Update the "future column" comment. |
| `nora/infra/sql/migrations/versions/0003_quoted_ts.py` (**new**) | Hand-written `op.add_column`. Docstring mirrors `0002`'s. |
| `nora/infra/sql/audit.py` | `append` writes `quoted_ts` (same INSERT, `:65-77`); `recent` selects it (`:100-124`); `set_delivery_status` → `set_delivery_outcome(..., ts=None)` (`:129-141`); new `by_timestamps(...)` (~20 lines; file is 268 L — **watch the cap**, split `by_timestamps` into a sibling module if it lands with anything else). |
| `nora/domain/ports.py` (288 L) | `RecordStore.by_timestamps`; `SendResult.timestamp: int \| None = None`. **At the cap** — this plus §1.7's `MemoryStore.representation` is ~6 lines and puts the file at ~294. Plan a split. |
| `nora/platform/signal/send.py:80-85` | Read `timestamp` off the JSON-RPC result into `SendResult`. |
| `nora/agent/guards.py:110-145` (295 L, **at cap**) | Pass `result.timestamp` into `set_delivery_outcome`. Two lines — anything larger must move out first. |
| `nora/agent/prefill.py` (131 L) | `_transcript_block` renders `↳`; `_context_header` gains the legend; `build_tag_message` renders the *resolved* parent, never the envelope copy. ~25 lines. |
| `nora/agent/tools.py` | `read_buffer` adds `"reply_to"`. |
| `nora/testing/fakes.py` (281 L, near cap) | `FakeRecordStore` matches — `quoted_ts`, `by_timestamps`, `set_delivery_outcome`. |
| `tests/contract/test_audit_store_contract.py` | A `by_timestamps` / `quoted_ts` block across both stores (CLAUDE.md: a port never gains a second implementation without a shared contract suite). |
| `tests/infra/test_schema_parity.py` | Column set + append position. |
| `design/09-decision-index.md` | DEC-23; R-20. |
| `tests/test_acceptance_rules.py::RULE_TESTS` | `"R-20": "test_a_forged_quote_cannot_put_words_in_someone_elses_mouth"`. |

---

# Build order

**0. Unblock Honcho derivation — ops, not code, and it gates everything in Part 1.**
16 pending work units with 0 in progress; every conclusion belongs to the retired
peer `miles`. Until this is fixed, any recall feature ships as a no-op that looks
like a feature — bug class #6. Not a code change; do not let it hide inside one.

**1. `representation` for the tagger only, and delete `read_peer_card`.**
One client method, one line in `context.py:64`, one tool deleted. Smallest change
that makes recall non-empty, independently verifiable against the live instance,
and it needs nothing from the roster design.

**2. Bug B — the platform timestamp.** `SendResult.timestamp` → `Deliverer` → the
existing `set_delivery_status` write. Before the column, because it decides whether
the column is useful, it is independent of the migration, and it is testable today.

**3. `quoted_ts` — column, migration, `Quote` dataclass, in-window rendering.**
Ship the arrow. Fixes Bug A on the way past.

**4. Room-scoped multi-person recall fan-out** (`nora/engagement/recall.py`).
**Depends on the roster design** — `RosterStore`, `PlatformAdapter.rosters()`, R-19.
Do not start it before that lands; steps 1–3 and 5 do not need it.

**5. Out-of-window resolution + `read_buffer` consistency.** Both polish. Last.

# Open questions

1. **Is Honcho's deriver meant to be running?** 16 pending / 0 in progress. And every
   conclusion belongs to peer `miles`, retired in favour of `miles_novak` — migrate them,
   or write them off?
2. **A room member's recalled facts, spoken back into that same room — acceptable?**
   R-18 blocks identifiers; nothing blocks *"Tyler told you he was broke."*
   `constraints.md` line, a low `max_conclusions`, or a hard filter? Only you can call it.
3. **Token budget.** 5 conclusions × 6 people, or 8 × 3? Recommendation: 5 per person,
   only for people appearing in the 40-row window or named in the tag message.
4. **Should Nora exist as a Honcho peer?** `target=` 404s without it. DEC-9 says her
   *account* never goes in `identity.toml` — a Honcho peer is a different object, and
   this is a separate decision.
5. **`chat` at 7–8 s — worth a CLI?** `nora recall <person> "<question>"`, off the tag
   path, where the latency is yours to spend. Or not worth building.
6. **Backfill `quoted_ts` from `raw_json` in `0003`, or start clean?** 20 rows exist,
   one carries a quote. Recommendation: start clean.

# Not verified against live services

- **signal-cli's `send` response carrying `timestamp`.** `SignalSender.send`
  (`send.py:80-85`) discards the body today, and no live send was made. §2.3 depends
  on it; verify with one `curl` to `/api/v1/rpc` before building step 2.
- **`authorName`'s absence from `quote`** rests on the single stored envelope in
  `data/messages.db` (row 21) plus the OpenAPI-free JSON shape. signal-cli's source
  was not read.
- **`representation` latency at scale** measured on a peer with 8 conclusions.
  Behaviour at 100+ is unknown; `max_conclusions=5` bounds the response, not the work.
- **How Honcho populates `peer_card`** — whether only via `PUT /peers/{id}/card`, or
  by dreaming. `/schedule_dream` exists and was not invoked.
- **The roster design** is not in the repo (no `RosterStore` / `rosters()` / `R-19`
  anywhere under `nora/`, `tests/` or `design/`), so §1.5 and step 4 build on a
  described, unwritten design.
- **Token-cost estimates** for the recall block are estimates, not measurements.
