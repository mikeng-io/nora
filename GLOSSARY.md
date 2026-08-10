# Glossary

Words in this repo that mean something specific, and words that have collided.

Written 2026-08-06, after a naming discussion surfaced three terms doing double
duty and two tools whose names describe something they are not. **A tool's name
is the model-facing schema** (Strands derives it from the callable), so a
misleading name misleads the agent, not only the reader.

Ordered by how much confusion each has actually caused.

---

## Identity — three different things

The worst collision in the codebase. All three are live, all three are called
"identity" somewhere.

| term | means | where |
|---|---|---|
| **identity (resolution)** | mapping a platform *account* — phone number, ACI — to a person | `nora/ingest/stages/identity.py` |
| **`PlatformIdentity`** | **Nora's own account** on a platform, used for self-tag checks (R-12) | `domain/models.py`, `adapter.resolve_identity()` |
| **`doctrine/identity.md`** | who Nora *is* — character, not accounts | the doctrine layer |

Because of this, tools that answer *"who is 阿豹?"* are named **`person_*`**,
never `identity_*`. See Person below.

## Person, and the things that are not a person

- **person** — a human being. Identified by **`person_id`** (`miles_novak`), which
  is stable, operator-assigned, and is also their Honcho peer id (A2, DEC-14).
- **`PersonRef`** — the resolved record: person_id, name, first/last, aliases.
- **account** — one platform login. **One person may hold several** (DEC-14);
  the Ridgeline group contains three accounts and two people, one of whom is Nora.
- **alias** — a nickname the *operator* wrote in `identity.toml` (阿豹妹,
  峰哥). Trusted, because a human on disk authored it.
- **display name** — what someone set on their own Signal profile.
  **Untrusted** — anyone can set it to anything, which is why design/10 §1.3
  forbids an alias or display name ever resolving a *sender*. Resolving a
  lookup *target* is a different, safe operation.
- **unmapped sender** — an account with no person in `identity.toml`. Reported
  as a **count**, never as an id, because the only thing Nora holds for them is
  a phone number or ACI. `nora_unmapped_senders_total` is the tell.

## Memory — record versus interpretation

Three layers, two stores, and the distinction that matters most:

| layer | store | holds | tool |
|---|---|---|---|
| **hot** | PostgreSQL | last 40 messages, verbatim | *(injected, no tool)* |
| **deeper** | **same PostgreSQL** | everything older, verbatim | `read_buffer` |
| **derived** | Honcho | conclusions *about people* | `about` |

**The record store is the RECORD. Honcho is the INTERPRETATION.** The record
store is authoritative and is what R-2/A9 guarantee; Honcho is best-effort and
may be absent — which is why `nora/infra`'s two clients have opposite error
contracts.

- **`RecordStore`** (port) / **`SqlRecordStore`** (its one concrete, over
  `postgresql+asyncpg://`) — the record store's read/write seam. **Renamed from
  `AuditStore`/`SqlAuditStore` in the 2026-08-07 PostgreSQL migration**
  (design/20): "audit" read as a compliance log, but this store IS the record
  (hot + deeper above), not a side-channel of it. `FakeRecordStore`
  (`nora.testing`) is its contract-bound double. Was SQLite until that
  migration; the store is now one `nora` PostgreSQL database that presence
  (design/19) will share.

- **`read_buffer` was misnamed** — nothing is buffered; it reads durable rows
  from disk. **Renamed to `chat_earlier` 2026-08-07 (DEC-58), then to `load_transcript` 2026-08-08**, the honest name,
  after the MEASURED selection harness confirmed no regression (16/16 on its own
  question, no collision onto other tools). The `nora_tool_calls_total{tool}`
  label moves with it (it must agree with the `_log_call` grep); the metric NAME
  is unchanged (append-only).
- **`recall` was misnamed** — it fetched an accumulated *profile*, not
  memories. **Renamed to `about` 2026-08-06 (DEC-39).** The metric
  `nora_recall_total` keeps the old word: metric names are append-only.
- **representation** — Honcho's derived profile of a peer. Measured 2026-08-06
  as ~88% *events* ("asked what time it was") rather than *traits*. Under
  review; do not build on it until settled.
- **peer card** — a Honcho endpoint that **returns null in this deployment**
  (DEC-27). Not to be confused with the transcript's `<peer_card>` block, which
  is populated from elsewhere.
- **nothing found ≠ could not ask.** `read_buffer` empty means *nothing was
  said*. `about` empty means *nothing was concluded* — weaker. Hence
  `Recollection.reachable`. Collapsing the two hid a dead endpoint for the life
  of the deployment.

## Doctrine — what was decided

**Doctrine**, not "persona". Renamed 2026-08-06 (DEC-29): "persona" names a
*character*, and a directory named for a character invites sentences about what
that character is like — which is how a capability claim ended up in
`soul.md` and went silently false.

Five `kind`s, and the line through the middle is the whole design:

| kind | source of truth | authored? |
|---|---|---|
| `constraint` | the operator's decision | yes |
| `identity` / voice | the operator's taste | yes |
| `conduct` | the operator's judgement | yes |
| **`capability`** | **the tool registry** | **no — generated** |
| **`situation`** | **the running turn** | **no — generated** |

A capability claim in an authored file now **fails to load**.

## A turn

- **ambient** — a message Nora records but does not answer. The default.
- **tag** — a message that triggers her. One tag = one turn.
- **turn** — everything from admission to reply. Contains several **model
  calls**; each call is a **round trip** (~4s) and each retained tool result is
  **re-sent on every subsequent call**, which is why intake compounds.
- **fire turn** — a turn nobody asked for: same stages, same guards, but no tag
  behind it. The second of exactly two paths that may send (R-1b).
- **occasion** — WHY a fire turn is happening, as a value (`nora.app.fire.Occasion`):
  where she speaks, on whose authority, the words put to her, and the header
  that explains the moment. Two exist — a reminder coming due, and a version
  announcement (DEC-79). A reminder names its requester; an announcement names
  NOBODY, which is what stops the turn being refused under a rule about a grant
  it never held.
- **terminal verb** — a tool that ends the turn. Today only `send_reply`, on
  both paths — a fire turn has no way to choose silence (that waits for the
  staleness check).
- **prefill** — the three messages built before the tag message.
- **context header** — the block explaining how the turn works. Its opening
  paragraph is the one thing that differs between a tag and a fire turn:
  "somebody just called you" versus "nobody has, and here is why you are awake"
  — and between the two fire occasions, which is why the occasion carries it.

## Reminders

- **reminder** — a *deferred request*: a row holding what someone asked for and
  when it comes due. Not a stored message to replay — at fire time it is put to
  her as a request and she answers it, so anything she can do now she can be
  reminded to do later.
- **heartbeat** — the tick that wakes on an interval, finds due rows and runs
  their turns. Knows that time passed; knows nothing about why it matters.
  (Not `nora.obs.logging.heartbeat`, which is a 60s counters log line.)
- **claim** — `pending → firing` as a compare-and-set inside the statement, not
  a read-then-write. This is what makes a row fire *once, ever* (R-37).
- **at-most-once** — the decision behind a crash. A row left `firing` by a dead
  process resolves to `failed`, never re-queued: losing one reminder visibly
  beats sending one twice (R-37b).
- **the slot** — one in-flight turn per conversation, shared by both paths, so
  a reminder and a tag can never run at once in the same room. Held by
  `ConversationSlots`; a tick that cannot take it defers to the next one.
- **ownership** — group-owned in a group, person-owned in a DM. Not enforced
  separately: it falls out of cancellation being scoped to the conversation.

## Guards

- **egress** — anything leaving Nora. Two predicates since 2026-08-06:
  **`scan_identifiers`** (phone numbers, ACIs, keys — unsafe *anywhere*) and
  **`scan_reply`** (those plus model names — replies *only*, since she must
  never name the model she runs on).
- **canary** — a string planted in the doctrine. If it appears in her output
  she is regurgitating her prompt, and the reply is blocked. A **tripwire, not
  a watermark** — it is not something she emits.
- **fails closed** — a guard that blocks the *whole* reply on any hit, so she
  says nothing rather than something unsafe.
- **R-5 / silent failure** — Nora is **silent when broken**, so broken is
  indistinguishable from quiet. Nearly every design decision here is shaped by
  making failures *legible* instead.
- **decorative test** — one that passes whether or not the code works. The
  standard: *if reverting the code under test does not turn it red, it is not a
  test.*

## Scope

- **room** — the conversation a tag happened in, resolved to people
  (`nora/agent/room.py`). The scope every room-scoped tool resolves *within*.
- **resolve within scope**, never *resolve then filter* — a person in
  `identity.toml` but not in this room is **absent from the index**, so "not
  here" is indistinguishable from "does not exist" (R-21). Filtering afterwards
  would leak membership through the difference.
- **enforced at assembly, not at egress** — by the time text exists the
  disclosure is already authorised. The 2026-08-06 roster leak is the proof:
  R-18 saw nothing to block because names are not phone numbers.

## Rule prefixes

- **R-n** — a behavioural rule. Registered in `design/09-decision-index.md`
  and named in `tests/test_acceptance_rules.py::RULE_TESTS`.
- **DEC-n** — a decision, with its rationale.
- **A-n** — an architectural invariant from the original spec.

R-19 is **reserved but never assigned** (design/11). Next free: R-25.
