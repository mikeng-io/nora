---
kind: design
title: "Nora — Identity and Policy Schema"
description: "A platform-neutral schema for who people are (identity.toml) and what is allowed (policy.toml), designed against the real identifier shapes of Signal, Telegram, Discord and WhatsApp."
tags: [nora, design, identity, policy, allowlist, multi-platform]
timestamp: '2026-08-05'
status: proposal
edges:
  - {dst: 04-config-identity.md, rel: amends, provenance: asserted}
  - {dst: 09-decision-index.md, rel: extends, provenance: asserted}
---

# Identity and Policy Schema

**Status: proposal.** Nothing here is built. It exists so the schema is settled
before a second platform lands, because the schema is the thing that decides
whether platform four is an edit or a rebuild.

## 1. The problem, stated precisely

Today `identity.toml` maps **one id per platform per person**
(`nora/persons.py:55`, `platforms: dict[str, str | int]`), and the Signal parser
reads **one** sender field (`parser.py:35`,
`source or sourceNumber`). Both assume a person has a single, stable,
operator-obtainable identifier on each platform.

**That assumption is false on all four target platforms, for different reasons.**

### 1.1 Stability and obtainability are different axes

| Platform | Stable id Nora receives | Can the operator obtain it? | Human-readable id | Stable? |
|---|---|---|---|---|
| **Signal** | ACI (UUID) | **No** — never exposed in the UI | phone (E.164), username | phone is hideable; username changeable |
| **Telegram** | `user.id` (int64) | **Not directly** — needs bot interaction | `@username` | changeable, optional |
| **Discord** | snowflake `user.id` | **Yes** — Developer Mode → Copy ID | `username` | changeable |
| **WhatsApp** | LID | **No** | phone JID | phone is stable-ish; no usernames |

The identifier Nora *receives* and the identifier the operator *can write down*
are usually **not the same value**. Discord is the only platform where they
coincide.

This is the whole reason a schema change is needed. It is not a nicety: with a
hidden phone number on Signal, the operator **cannot author a working mapping at
all**, because the only id Nora sees is one he can never look up.

### 1.2 Consequence: discovery must precede confirmation

Since the operator cannot obtain the stable id, **Nora must surface it** and the
operator confirms a binding against something he *can* recognise — a display
name, or a phone/username that was visible at least once.

This does not violate D6 ("peer mapping is manual, one operator-maintained
file — no service, no UI"). The file stays authoritative and hand-edited; Nora
only ever *suggests*. Spec §11 already sanctions exactly this:
*"`nora persons suggest` CLI emitting `identity.toml` stubs for operator
confirmation. Does not violate D6."*

### 1.3 Display names are never identifiers

WhatsApp push names and Signal profile names are **user-controlled and
spoofable**. Telegram display names likewise. Matching a person on a display
name is trivial impersonation.

**Rule: display names may be shown to the operator, and may be rendered in a
transcript. They are never matched on.** This is a security invariant, not a
style preference.

## 2. Conversation addressing

### 2.1 `conversation_id` stays a flat string — deliberately

`conversation_id: str` is load-bearing in eleven modules: the SQLite column and
its `ts DESC` index (`infra/sql/schema.py`), R-3's send target check
(`agent/guards.py`), the debounce and in-flight keys (`tag.py`), the
`read_buffer` tool, and `ConversationTarget`'s hashability.

Discord is the stressor: its conversation unit is a **channel inside a guild** —
two levels where the other three are flat.

**Decision: adapters canonicalise to a flat string; the domain never learns
about hierarchy.** Discord's adapter emits `"<guild_id>/<channel_id>"`. The
domain model is unchanged, the index is unchanged, R-3 is unchanged.

| Platform | Canonical `conversation_id` |
|---|---|
| Signal | `groupId` (base64) |
| Telegram | `chat.id` (stringified int) |
| Discord | `"<guild_id>/<channel_id>"` |
| WhatsApp | group JID (`…@g.us`) |

### 2.2 Telegram's migrating chat id

When a Telegram group is upgraded to a supergroup, `chat.id` **changes**, and
the old id is reported once via `migrate_to_chat_id`. A policy keyed on the old
id silently stops matching — Nora goes quiet, which under R-5 is invisible.

**The schema must allow a conversation to carry more than one id**, for the same
reason a person must. This is not Telegram-specific pedantry: it is the same
shape as the person problem.

## 3. Proposed `identity.toml`

Hand-editable (D6), one section per person, ids typed by **kind** so matching
can reason about them.

```toml
[persons.miles]
name = "Miles"

# Every id form Nora may see for this person, per platform.
# Keys are the id KIND; the vocabulary is fixed per platform (§3.1).
[persons.miles.signal]
phone    = "+85212345678"
uuid     = "8185b8fa-1234-5678-9abc-def012345678"   # discovered by Nora, confirmed by you
username = "miles.42"

[persons.miles.telegram]
user_id  = "123456789"
username = "pinegrove"

[persons.miles.discord]
user_id  = "100000000000000003"

[persons.miles.whatsapp]
phone    = "+85212345678"
```

`honcho_peer_id == person_id` is unchanged (A2): **one human, one peer, across
every platform.** That is what makes Discord-Miles and Signal-Miles the same
person, and it already works — it is the *mapping* that was too narrow, not the
peer model.

### 3.1 Id-kind vocabulary

Fixed per platform so a typo is a startup error under `extra = "forbid"`, not a
silently-ignored key.

| Platform | Kinds | Matchable | Notes |
|---|---|---|---|
| signal | `phone`, `uuid`, `username` | all | `uuid` is the ACI; operator gets it from Nora |
| telegram | `user_id`, `username` | all | `user_id` is stable; `username` changeable |
| discord | `user_id`, `username` | all | `user_id` obtainable via Developer Mode |
| whatsapp | `phone`, `lid` | all | no usernames |
| *any* | `display_name` | **never** | §1.3 — shown, never matched |

### 3.2 Matching

The reverse index becomes `(platform, id_value) -> person_id`, built from
**every** kind. Duplicate detection stays (`persons.py:100`) — the same value
mapped to two people is still a hard startup error, since an ambiguous identity
is worse than none.

Precedence matters only for *reporting* ("Nora matched you by uuid"), not for
correctness: any listed id resolves to the person.

## 4. Proposed `policy.toml` — two allowlists

A **third change cadence**, the same argument DEC-8 used to split
`identity.toml` from `config.toml`: config changes on deploy, identity when a
human joins, **policy when trust changes**.

Two orthogonal grant sources:

1. **Principal allowlist** — who may talk to Nora *directly* (DMs).
2. **Conversation allowlist** — which rooms Nora participates in, and who
   within them.

### 4.1 Why two lists and not one

A DM has **no stable conversation to allowlist** — its identity *is* the
person. Modelling it as a conversation entry means inventing a synthetic
`conversation_id` that is really a person id in disguise. Two lists say the
honest thing: DMs are authorised by principal, rooms by room.

### 4.2 Shape

```toml
default = "deny"

# ── 1. Principals: who may DM Nora directly ──────────────────────
[dm]
persons = ["miles"]        # person_ids from identity.toml. [] = no DMs at all.

# ── 2. Conversations: which rooms, and who within them ───────────
[[conversations]]
platform = "signal"
id       = "aBcDeF…base64"
label    = "Family"                  # for humans; NEVER matched on (§1.3)
persons  = ["miles", "ada"]

[[conversations]]
platform = "telegram"
id       = "-1001234567890"
also_ids = ["-987654321"]            # pre-supergroup-migration id (§2.2)
label    = "Work"
persons  = "*"                       # anyone in the chat may trigger

[[conversations]]
platform = "discord"
id       = "9876543210/1234567890"   # guild/channel (§2.1)
label    = "#general in Ridgeline"
persons  = ["miles"]
```

### 4.3 Grant semantics

```
admitted(sender, conversation) =
    conversation.type == "dm"
        ? sender.person_id in dm.persons
        : conversation is listed
          AND ( persons == "*" OR sender.person_id in persons )
```

`persons = "*"` means any sender in that room, **including unmapped ones**. It
does not disable the conversation allowlist. An unmapped sender still gets no
Honcho feed (D6 unchanged) — they are simply not refused.

### 4.4 No roles. Membership is normalised to exactly two values.

**Arbitrary named roles are dropped from this design entirely.** They existed
only on Discord; the other three platforms have a fixed membership status. A
grant vocabulary where a value is meaningful on one platform of four is
divergence wearing an abstraction's clothes.

Membership status itself **is** normalised, to exactly two values:

```python
class Membership(Enum):
    ADMIN  = "admin"
    MEMBER = "member"
```

| Platform | native → normalised |
|---|---|
| discord | guild owner, or Administrator permission → `admin`; else `member` |
| telegram | `creator`, `administrator` → `admin`; `member` → `member` |
| whatsapp | `superadmin`, `admin` → `admin`; `participant` → `member` |
| signal | `admin` → `admin`; `member` → `member` |

**Why two values and not three.** A three-value set (`owner`/`admin`/`member`)
looks more expressive, but **signal-cli does not distinguish the group creator
from any other admin**. `owner` would therefore be a value that silently never
matches on Signal — the precise divergence this is meant to eliminate. Two
values are genuinely universal; owner/creator/superadmin all collapse into
`admin`.

Telegram's `restricted` / `left` / `kicked` are not membership grades but
*absence*: such a sender is simply not a current member and their messages do
not arrive as ordinary group traffic. They map to no `Membership` value.

**`Membership` is descriptive, not authoritative.** It is carried so the
operator control plane can show *"Family — 3 members, you are an admin"*, and
so an adapter has a normalised word for what it observed. **Policy does not
grant on it.** Two reasons:

- **Hot-path I/O.** Deciding "is this sender an admin?" is a per-message
  platform lookup on Telegram and often on Signal. `EngagePolicy.should_engage`
  is `async` and stage 5 already degrades closed
  (`ingest/stages/gate.py` → `reason="policy_error"`), so the seam exists — but
  every ambient message would pay a round trip on the path that must not stall
  the SSE reader, and it would need a TTL cache to be viable.
- **Delegation.** Granting by membership hands authorisation to whoever can
  promote a group admin. The policy file would stop being a complete
  description of who has access — a real trade for a private agent, and not one
  worth making for a convenience.

Adding `admins = true` later is a one-line grant in §4.3's predicate if that
trade ever becomes worth it. Nothing here forecloses it.

### 4.5 DMs are not reachable yet

`parser.py:33` drops every non-group message — *"DM — out of scope in v2"*. The
`[dm]` section is **forward-looking**: it defines the grant model now so the DM
path has something to satisfy when it lands, rather than inventing
authorisation later under pressure. It should be parsed and validated from day
one, and a non-empty `[dm].persons` while DMs are still dropped deserves a
startup warning — otherwise it silently does nothing, which is the failure mode
this project keeps hitting.

### 4.6 What does NOT belong here

- **Tool budgets** — `ToolSpec` in the `ToolRegistry` (DEC-10).
- **Endpoints, ports, timeouts** — `config.toml`.
- **Who people are** — `identity.toml`. Policy references `person_id` only,
  never a raw platform id: adding Miles's Discord account then grants him access
  everywhere `miles` is already allowed, one edit rather than N, and the policy
  file stays readable without knowing anyone's phone number.

## 5. Nora's own identity

`PlatformIdentity.aliases: frozenset[str]` (DEC-9) is the same
one-person-many-ids problem applied to Nora herself, and it is currently
**dead in production**: `derive_aliases` reads `/api/v1/accounts`, which returns
404 on this daemon, so `aliases` degrades to `{primary}` on every start and R-12
falls back to equality.

**It should use the same typed-identifier model** rather than a bare string set,
so that "Nora's own ids" and "a person's ids" are one concept with one matching
rule, and so a degraded discovery is *counted* rather than silently logged.

## 6. What this unblocks

- **The operator control plane.** `nora persons pending` reads unmapped senders
  straight out of `messages.raw_json` — Nora already stores the full envelope for
  every message, mapped or not, because audit (ingest stage 2) runs *before*
  identity (stage 3). **No new storage is required.** It prints every id form
  seen next to the display name the operator recognises, and emits a
  paste-ready `identity.toml` stanza.
- **`nora groups`** lists conversations with `label` + id + allowed/denied.
- **Telegram, Discord, WhatsApp** become adapter work plus a vocabulary row,
  not a schema migration.

## 7. Resolved decisions

1. **Explicit person lists are the default.** `persons = ["miles", "ada"]`; a new
   group member is ignored until added. Confirmed with the operator 2026-08-05.
   `persons = "*"` remains available per conversation for lower-friction rooms.
2. **`label` is never matched on.** It is a comment for humans (§1.3).
3. **Migration is a hard break.** `identity.toml` is gitignored, single-operator
   and currently holds one placeholder entry, so accepting the old
   `signal = "+852…"` shorthand would buy nothing and leave two shapes to
   support forever. The loader raises a readable error naming the new form.
