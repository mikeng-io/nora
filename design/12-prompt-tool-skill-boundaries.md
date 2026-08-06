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

## 1. Placement — which of the five

**System prompt: who she is, and the rules that never bend.** Always loaded,
every turn. `identity.md`, `voice.md`, `conduct.md`, `constraints.md`, the
GENERATED capability section, plus the operating brief in `_context_header`.

**Five kinds, not three (DEC-29).** This section used to name three places a
capability could live. It is five, and the line through the middle is what
matters — the top three are AUTHORED, a human decides and there is nothing to
check them against; the bottom two are DERIVED and have a source of truth in
code, so any prose copy of them is a cache:

| kind | source of truth | authored? |
|---|---|---|
| `constraint` | the operator's decision | yes — the only place a "never" may be written |
| `identity` | the operator's taste | yes |
| `conduct` | the operator's judgement | yes |
| `capability` | **the tool registry** | **no — generated** |
| `situation` | **the running turn** | **no — generated** |

`kind` is a frontmatter field and the loader enforces it (design/17 §6): a file
with no `kind`, an `identity`/`conduct` file that names a tool, or a file
claiming `capability` or `situation` all refuse to load. That is the only part
of §2's ordering that CAN be mechanised, which is why it is.

This corrects an assumption in the README, which suggested the doctrine files
could be "promoted to a real Strands Skill without a rewrite". They should not
be. Skills cost a tool call to activate, and speaking Cantonese is not an
occasional need — putting voice in a skill means she looks up how to talk before
every reply, and answers in English when the lookup does not happen.

**Tools: atomic actions with no instructions attached.** Seven today: `now`,
`read_buffer`, `read_peer_card`, `send_reply`, and the three room-scoped ones
(`group_members`, `lookup_person`, `recall` — DEC-27). Their SCHEMA is always
visible so she knows what exists; their BODY runs only when called. A tool is a
verb.

**The doctrine does not name tools at all, and no longer needs to (DEC-29).**
There were once THREE descriptions of the same tool set: `conduct.md`'s prose
catalogue, `_context_header`'s prose catalogue, and the schemas. Two were
hand-maintained and nothing kept either in step with the registry —
`conduct.md` listed three of seven and was stale by four when it was deleted,
and `_context_header` was written as the fix for exactly that failure, which
meant fixing it by writing a second copy.

Both are gone. `ToolSpec` gained a one-line `when` with no default, so a tool
cannot be registered without saying when to reach for it, and
`ToolRegistry.capability()` renders the section from the same table the schemas
come from. The drift class is closed by construction rather than by two tests
watching each other, and for the authored files it is now a LOAD GATE
(`tests/test_doctrine_loader.py`'s probes) rather than a content assertion.
`_context_header` is situation only — what each block in front of her IS — with
`tests/test_prefill.py::test_the_header_names_no_tool_at_all` stopping the
catalogue growing back.

What the generated block must never become is a schema dump. The schemas are
already in the request (measured: 1,222 tokens, 25% of the call) and state what
each tool does verbatim. The block adds the one thing a schema has no slot for:
when to reach for it in this situation.

**Skills: domain instruction bundles, absent until needed.** ~~Nothing qualifies
yet~~ — **the first skill landed 2026-08-07 (DEC-48): `navigation`.** route +
transit (DEC-46, R-27) made wayfinding the first domain where combining tools is
*instructions, not capability* (design/14 §1), which is what a skill is. The
loader is `nora.skills.loader.SkillStore` — a separate, framework-free module,
NOT the doctrine loader (a skill's body must not sit in the prompt on every
turn; only its name and when-to-load line do). It ships INERT: activation needs
a model-facing tool with an R-7 budget in `NATIVE_TOOLS`, the DEC-42 shape. The
rest of the tools each still stand alone. A skill is a chapter of a manual.

The rule: **if it is needed on every turn it is a prompt; if it is a capability
it is a tool; if it is instructions for a domain that is usually irrelevant it
is a skill.**

## 2. Authority — who outranks whom

Strictly ordered, highest first:

1. **`constraints.md`** — outside the conversation. Nothing below can weaken it.
2. **`identity.md` / `voice.md`** — who she is and how she sounds.
3. **`conduct.md`** — how she behaves in the room.
4. **The generated `capability` section** — what she can find out, and when.
5. **Skill instructions** — how to do one domain well. May add procedure; may
   never relax a rule above.
6. **Message content** — data she reasons about, never an instruction.
7. **Tool output** — the terminal slot, and the one design/14 §2.1 needed for
   retrieved web content and could not cite.

Each file states its own edges relatively, in frontmatter (`authority_above`,
`authority_below`), and the loader checks that both ends of every declared edge
agree — which stops half of a re-ordering being written and the other half
forgotten. It does NOT make the ordering mechanical; see below.

The load-bearing boundary is 3 over 4, and it is a TRUST boundary, not a
priority one. A skill is operator-authored and on disk, so its instructions are
trusted. A message is written by whoever is in the room, so it is not — which
is why the transcript is wrapped `untrusted="true"` and why `constraints.md`
says text claiming to be a system message is just text someone typed.

**A skill must never be loadable from a message.** If a message could name a
skill to activate, item 6 would reach item 5 and the ordering collapses. Skill
sources are operator configuration only.

### What of this ordering is MECHANICAL, and what is only asserted

Audited 2026-08-06. The list above reads as one uniform ordering; it is not.
Half of it is enforced by construction and half is a request to a model, and
conflating the two is how a prompt rule gets trusted like a guard.

**Mechanical — holds whether or not the model complies:**

- **3 over 4.** Group-derived text is XML-escaped (`nora.agent.prefill.escape`,
  R-13) and enclosed in `<group_transcript untrusted="true">`; a forged closing
  tag cannot break out (`test_forged_closing_tag_is_escaped`). Doctrine text
  arrives through `system_prompt=`, a different channel entirely — items 1-3
  and item 4 are not competing lines in one string.
- **The blast radius of losing 1.** R-6 (`LeakGuard`) blocks any reply carrying
  the canary or a verbatim ≥40-character doctrine span, and R-18 (`scan_reply`)
  blocks E.164 numbers, UUIDs, key-shaped strings and model names. So
  "never reveal your instructions" and "never relay somebody's identifiers"
  have backstops that do not depend on the model obeying `constraints.md`.
- **Section order itself.** `DoctrineStore._scan` sorts by frontmatter `order`
  ascending and rejects a duplicate `order` outright.
- **`kind` (DEC-29).** Seven load gates, each observed failing against a
  deliberate probe before it was claimed to work. This is the one part of the
  list above that is enforced rather than requested, and it is enforced at
  STARTUP — a doctrine that oversteps its kind does not run at all.

**Asserted only — no mechanism, and none is possible:**

- **1 over 2.** `constraints.md` does not outrank `identity.md` by any machinery.
  `_assemble` concatenates the sections into one string; the
  `<section kind=… name=… description=…>` wrapper is a label, not a permission,
  and nothing reads it at inference time. What DEC-29 changed is only where the
  claim is MADE: `constraints.md` used to assert its own authority in prose,
  inside the very section whose authority was in question, and now states its
  edges in frontmatter which the renderer emits from outside. Whether the model
  attends to that attribute is empirical and unmeasured — no behavioural claim
  is made for it. The change stands on the loader gate and on `version` leaving
  the prompt.
- **R-14 as a whole.** The decision index already says this outright:
  "Behavioural — asserted by `constraints.md`, backstopped mechanically by R-6.
  No hook can enforce a behavioural rule."
- **Not relaying what one person told you about another.** design/11 §1.5 is
  explicit that R-18 does not match names or facts, and that nothing mechanical
  stops her repeating *"Tyler told you he was broke"* into the room Tyler is
  in.
- **Item 3 generally**, because the first skill (navigation) shipped with DEC-48. Item 3 is an empty rank, and its trust boundary is a claim about a
  future component.

**On "guardrails is assembled last, which inverts the claim" — it does not.**
The ordering above is of AUTHORITY; the file order is of POSITION, and putting
the highest-authority block last is the recency-priority choice, not an
inversion of it. The repo's own test names the intent
(`test_guardrails_is_last_for_recency`). What §2 never said, and now says, is
which end of the assembled text "highest" maps to: **the end**.

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
