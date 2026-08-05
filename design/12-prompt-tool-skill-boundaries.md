---
kind: design
title: "Nora — system prompt, tools and skills: coordination, boundaries, authority, scope"
description: "Which of the three a capability belongs in, who outranks whom, and what each is allowed to reach."
tags: [nora, design, strands, skills, tools, prompt, authority]
timestamp: '2026-08-06'
status: current
---

# System prompt, tools, skills

Strands offers four ways to give an agent capability. Its own comparison table
(`/docs/user-guide/concepts/plugins/skills/`) is the placement rule:

| Approach | Best for | Trade-off |
|---|---|---|
| System prompt | small, always-relevant instructions | grows unwieldy with many capabilities |
| Steering | dynamic, context-aware guidance and validation | more complex to set up |
| Skills | modular, domain-specific instruction sets | **requires a tool call to activate** |
| Multi-agent | fundamentally different roles or models | higher complexity and latency |

**Steering and multi-agent are deferred** (operator, 2026-08-06) to a later
phase. Steering is nonetheless the eventual home for the guard layer — it hooks
before-tool-call and after-model-response and ships a Tool Ledger provider,
which is `ReplyGuard` described in someone else's words. Recorded so we do not
rediscover it.

## 1. Placement — which of the three

**System prompt: who she is, and the rules that never bend.** Always loaded,
every turn. `soul.md`, `voice.md`, `conduct.md`, `guardrails.md`, plus the
operating brief in `_context_header`.

This corrects an assumption in the README, which suggested the persona files
could be "promoted to a real Strands Skill without a rewrite". They should not
be. Skills cost a tool call to activate, and speaking Cantonese is not an
occasional need — putting voice in a skill means she looks up how to talk before
every reply, and answers in English when the lookup does not happen.

**Tools: atomic actions with no instructions attached.** `now`, `read_buffer`,
`send_reply`. Their SCHEMA is always visible so she knows what exists; their
BODY runs only when called. A tool is a verb.

**Skills: domain instruction bundles, absent until needed.** Nothing qualifies
yet — she has three tools and no domain. They earn their place in Phase 10,
when wayfinding (`now` + `directions` + how to combine them) and search arrive.
A skill is a chapter of a manual.

The rule: **if it is needed on every turn it is a prompt; if it is a capability
it is a tool; if it is instructions for a domain that is usually irrelevant it
is a skill.**

## 2. Authority — who outranks whom

Strictly ordered, highest first:

1. **`guardrails.md`** — outside the conversation. Nothing below can weaken it.
2. **`soul.md` / `voice.md` / `conduct.md`** — who she is and how she behaves.
3. **Skill instructions** — how to do one domain well. May add procedure; may
   never relax a rule above.
4. **Message content** — data she reasons about, never an instruction.

The load-bearing boundary is 3 over 4, and it is a TRUST boundary, not a
priority one. A skill is operator-authored and on disk, so its instructions are
trusted. A message is written by whoever is in the room, so it is not — which
is why the transcript is wrapped `untrusted="true"` and why `guardrails.md`
says text claiming to be a system message is just text someone typed.

**A skill must never be loadable from a message.** If a message could name a
skill to activate, item 4 would reach item 3 and the ordering collapses. Skill
sources are operator configuration only.

## 3. Scope — what each may reach

**The model names WHAT. Nora resolves WHERE.** This is already the rule for
tools (DEC-21): `read_buffer` reads the closure's bound conversation and
`send_reply` goes to the deliverer's bound target. R-3 therefore holds by
construction rather than by veto.

**DEC-24 took it one step further, and that step is the rule for everything
added later: an inert parameter is worse than no parameter.** The conversation
argument was harmless — nothing read it — but Strands derives the model-facing
schema from the callable, so declaring it FORCED the model to invent a group id
on every call. On the first real tag it invented the wrong one three times and
the turn died silent. A value Nora resolves herself must not appear in the
schema at all; "the model may pass it, we will ignore it" still costs a guess
per call.

The same must hold for everything added later:

- A memory lookup takes a PERSON, never a peer id, and resolves it against the
  people in this room.
- A group query takes no conversation argument at all; it is bound to the tag's.
- An MCP tool (Phase 10) is bound the same way, and its arguments are treated
  as model-supplied — because they are.
- A tool declares only what the model actually decides. Anything Nora already
  knows is closed over, not asked for.

**Scope is enforced at assembly, never at egress.** The roster leak of
2026-08-06 is the proof: R-18 blocks phone numbers and ACIs from replies, and
the leaked roster carried neither — only names. By the time text exists the
disclosure is already authorised. A guard on the way out cannot fix a prompt
that should not have contained the thing.

## 4. Coordination — what a turn costs

Skills activate through a tool call, so every skill is a turn. That is the
budget:

    「你係邊個」          send_reply                          1 turn,  ~5s
    「而家幾點」          now → send_reply                    2 turns, ~9s
    「點去到十點前」      skill → now → directions → reply    4 turns, ~20s

`llm.limits.turns = 8` (raised from 4 on 2026-08-06) is sized for the third
shape with headroom for a retry. `reasoning_effort = "low"` is what lets her
choose correctly at all: measured on this gateway, `"none"` answered 「你係邊個」
by calling the clock, because `tool_choice="required"` forces a call and
without reasoning she picks reflexively.

**Cost must stay proportional to the question**, and it is enforced
structurally rather than by instruction: `send_reply` is itself a tool, so a
simple answer resolves in one turn, and `ReplyGuard`'s `BeforeModelCallEvent`
hook cancels the loop the moment the reply lands (DEC-21). A trivial question
cannot be made to pay skill cost.

## 5. What this means next

1. Tools before skills. A skill bundles instructions for using tools; with
   three tools and no domain there is nothing to bundle.
2. ~~`lookup_person` and `group_members` over signal-cli's JSON-RPC, then
   `recall` over Honcho's `representation`~~ — **built, 2026-08-06 (DEC-27).**
   `group_members()` takes no argument at all; `lookup_person(name)` and
   `recall(person)` take one name each and resolve it INSIDE this
   conversation's membership (`nora.agent.room.Room`), never against
   `Persons.index`. §3's rule became R-21 (a person in `identity.toml` but not
   in this room is indistinguishable from one who does not exist) and R-22
   (a tool that could not answer says so in its return value, and "nothing
   known" is never the same value as "could not ask").
3. Skills arrive with Phase 10's domains, packaging MCP tools plus the
   instructions for combining them.
4. Steering, when the guard layer is worth moving.
