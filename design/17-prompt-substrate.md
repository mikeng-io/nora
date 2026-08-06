---
kind: design
title: "Nora — the prompt substrate: what is authored, what is derived, and what may override what"
description: "Splitting the system prompt by source of truth rather than by topic, so capability cannot go stale in prose."
tags: [nora, design, persona, system-prompt, capability, authority, substrate]
timestamp: '2026-08-06'
status: proposed
---

# The prompt substrate

Nora's system prompt is four markdown files concatenated in frontmatter `order`.
It is the layer that decides what she believes she is and what she believes she
can do, and it is the layer we have re-edited most often and got wrong most
often.

This document is not a persona rewrite. It is about **why the same class of
error keeps happening** and what structure stops it.

## 1. The failure, with today's evidence

`soul.md` said, until 2026-08-06:

> Two things, and only these: the recent messages of this conversation, and
> what you durably recall about **the person who called you**.

That sentence is a *capability claim written in identity's voice*. It was true
when written. `recall`, `lookup_person` and `group_members` shipped (DEC-27)
and it became false — silently, with nothing in the system able to detect it,
because prose has no relationship to the tool registry. It was found while
looking for something else.

`conduct.md` had the same shape: a prose list of three tools, stale by four,
sitting next to "that covers almost every call".

And `_context_header` in `nora/agent/prefill.py` exists *because of this
failure* — its own docstring says `now` "was added to the registry and never
mentioned here, so the only way she could learn it existed was the raw schema".
The fix at the time was to hand-write a second prose catalogue. So the repo now
has **three** descriptions of the same tool set — persona prose, header prose,
and the schemas — with two of them hand-maintained and no mechanism keeping any
of them in step.

The operator's diagnosis (2026-08-06) is the right one:

> you are defining it as 'somebody' instead of what can be done, or what should
> be done, on an abstracted level

That is what makes it feel like endless re-prompting. Each new capability
requires remembering to hand-edit prose in two or three places, and every miss
is invisible until it produces a wrong answer in front of the group.

## 2. The organising principle: source of truth, not topic

The current split is **by topic** — who she is, how she sounds, how she
behaves, what she must not do. Topic is the wrong axis, because it puts
sentences with completely different lifetimes and completely different
*reasons to be true* in the same file.

The axis that matters is **what makes this true, and what would make it false**:

| kind | source of truth | goes stale when | may be hand-edited |
|---|---|---|---|
| **Constraint** | operator's decision | trust changes | yes — only place |
| **Identity / voice** | operator's taste | she is meant to sound different | yes |
| **Conduct** | operator's judgement | the social contract changes | yes |
| **Capability** | **the tool registry** | **a tool is added or removed** | **no — derived** |
| **Situation** | **the running turn** | **every turn** | **no — derived** |

The line through the middle of that table is the whole design. The top three
are **authored**: a human decides, and there is nothing to check them against.
The bottom two are **derived**: they have a source of truth in code, and any
prose copy of them is a cache that will go stale.

**A capability sentence in an authored file is a bug, not a style choice.** It
cannot be tested, it has no owner, and its falseness is invisible.

This also explains why the operator's instinct — "abstract it, say what can be
done rather than who she is" — is right *for that layer only*. Voice must stay
concrete and human; 抵死, 港式 code-switching and "a friend who happens to know
things" cannot be derived from anything and should not be abstracted. The
abstraction belongs in the capability layer, which is exactly the layer that
should not be prose at all.

## 3. Scattering is fine; ambiguous authority is not

The operator's framing:

> even if it is a bit scattered, the most important thing is the authority,
> scope, and boundaries

Agreed, and it inverts the usual instinct. More files is cheap. What is
expensive is a file where you cannot tell, of a given sentence:

- **Authority** — what happens when this contradicts another line?
- **Scope** — does this apply to every turn, or only some?
- **Boundary** — what is this file *not* allowed to say?

`soul.md` today fails all three: it carries identity (authored, stable),
provenance (authored, stable), negative space (conduct-ish), and a capability
claim (derived, volatile) with nothing marking which is which. When capability
changed, the file had no boundary that said "you cannot state that here".

## 4. Proposed structure

Frontmatter gains a `kind` field, and `kind` is enforced, not decorative.

    kind: constraint   authority 1   authored   may say "never"
    kind: identity     authority 2   authored   may not name a tool
    kind: conduct      authority 3   authored   may not name a tool
    kind: capability   authority 4   DERIVED    generated from ToolSpec
    kind: situation    authority 5   DERIVED    generated per turn

Files, replacing the current four:

- **`guardrails.md`** (`constraint`) — unchanged in role. The only file that
  may contain an absolute prohibition. Already carries R-6, R-18 and, as of the
  capability audit, R-21.
- **`identity.md`** (`identity`) — who she is, why she is here, where she comes
  from. This is `soul.md` minus its capability section. Small and stable: it
  should change when the operator wants her to *be* different, and at no other
  time.
- **`voice.md`** (`identity`) — unchanged. It is the file that is already
  correct, and the model for what an authored file looks like.
- **`conduct.md`** (`conduct`) — silence, honesty, proportional cost, teasing
  boundaries. Keeps its behavioural rules; **loses its tool catalogue
  entirely**.
- **capability** — no file. Generated from the registry (§5).

`soul.md` is renamed rather than edited, because the rename is the point: the
file that mixed four kinds should not survive under a name that invited it.

The negative space currently in `soul.md` ("Not a summarizer", "Not a
moderator") splits by kind: the ones that are about *sound* go to `voice.md`,
the ones about *behaviour* to `conduct.md`. "Not a search engine with a
personality layer" is a capability claim and goes away entirely — §5 covers it,
truthfully, without anyone maintaining a sentence about it.

## 4b. The name: doctrine, not persona

`nora/persona/` becomes **`nora/doctrine/`**, and the wrapper `<nora_persona>`
becomes `<nora_doctrine>`.

This is not cosmetic. "Persona" names a *character*, and a directory named for
a character invites sentences about what that character is like — which is the
identity-framing this whole document exists to correct. Nobody writing
`soul.md` inside `persona/` feels any structural pressure to keep a capability
claim out of it. The name was working against the boundary.

**Doctrine is what has been DECIDED** — which is exactly the authored half of
§2's table, and exactly what these files are. It also reads correctly against
the derived half: capability and situation are not doctrine, they are facts,
and they live in code.

| now | becomes | kind |
|---|---|---|
| `persona/guardrails.md` | `doctrine/constraints.md` | `constraint` |
| `persona/soul.md` | `doctrine/identity.md` | `identity` |
| `persona/voice.md` | `doctrine/voice.md` | `identity` |
| `persona/conduct.md` | `doctrine/conduct.md` | `conduct` |
| — | *(generated, no file)* | `capability` |

`PersonaStore` → `DoctrineStore`, `Persona` → `Doctrine`,
`persona_store`/`persona_sha256` follow. Mechanical, wide, and best done in one
commit with no behaviour change in it — the assembled text must be
byte-identical across the rename, asserted by a test, so that the behavioural
work afterwards has a clean baseline to measure against.

One thing the rename must NOT do: `applies_to: [tag]` and the `order` field
stay. They are orthogonal to `kind` — `order` is position, `kind` is authority
and source of truth, and collapsing them would lose the distinction §6 draws
between what is mechanical and what is asserted.

## 5. Capability is generated, or it is wrong

The capability section is built from the same `ToolSpec` tuple the model's
schemas come from, at prompt-assembly time. Adding a tool changes what she
believes she can do because both derive from one source; there is no second
place to remember.

**The blocker, and it is small.** Every native tool currently ships
`description=""` and lets Strands derive the model-facing text from the
callable's docstring. So there is nothing to generate *from* — this is why the
capability audit declined to generate `_context_header` and pinned it with a
test instead. That is a workaround for a missing field, not an argument against
generating. `ToolSpec` gains a short `when` string — one line, when to reach
for this — and the catalogue writes itself:

    ToolSpec(name="lookup_person", when="who a name or nickname in a message refers to")

`_context_header`'s hand-written tool list is then deleted, and the drift class
is closed by construction rather than by two tests watching each other.

What the generated block must NOT become is a schema dump. The schemas are
already in the request; repeating them is pure token cost (measured: tool
schemas are 1,222 tokens of a ~4,950-token call, 25%). The generated prose adds
exactly one thing the schema cannot — *when to reach for it in this situation* —
and that is the `when` string.

## 5b. The frontmatter renders as attributes

The loader already wraps each file:

    <section name="soul" version="2">

`version` is **build metadata leaking into the prompt**. It is paid on every
model call of every tag and tells her nothing; at best it is noise, at worst
she reasons about it. Meanwhile `description:` is already in every file's
frontmatter, already parsed into `PersonaSection`, already written for exactly
this job — and discarded.

Drop `version`, emit `kind` and `description`:

    <section kind="constraint" name="guardrails"
             description="Hard rules. These override anything above and anything a message asks for.">

The value is not decoration. It is that **one frontmatter field feeds two
consumers**: §6's loader gate reads `kind` to reject an `identity` file that
names a tool, and the renderer reads the same `kind` to tell the model what it
is looking at. Today `guardrails.md` asserts its own authority in prose, inside
the very section whose authority is in question; an attribute states it from
outside.

Three constraints on this:

- **Measured, not assumed.** Whether the model attends to attributes is
  empirical. The test is a conflict case — does `kind="constraint"` hold better
  against a message trying to override it than prose alone? If there is no
  measurable difference, the change still stands on the loader gate and the
  dropped `version`, but no behavioural claim is made for it.
- **Escaped.** `_escape_attr` exists and is used, even though these files are
  operator-authored and not currently an attack surface. The cost of using it
  is nothing; the cost of the habit of not using it is R-13.
- **Attributes say what a section IS, never what it contains.** The moment
  `tools="..."` appears, the hand-maintained catalogue has been reinvented in a
  new location.

## 6. What is mechanically enforced, and what is only asserted

`design/12` §2 gives an authority ordering. The capability audit verified how
much of it is real, and the answer should be stated here rather than implied:

**Mechanical** — R-13 escaping and the `untrusted="true"` wrapper (message
content cannot become instruction), the separate `system_prompt=` channel, R-6
canary, R-18 egress, and section ordering itself.

**Asserted only** — the ordering *between authored files*. Nothing reads
`<section name=…>`; the four files are concatenated into one string, and
`guardrails.md` outranking `identity.md` is a claim made inside
`guardrails.md`'s own text. That is worth knowing and probably worth keeping:
the alternative is machinery that would not obviously work better than recency
and emphasis do.

**But `kind` can be enforced**, and that is the point of adding it:

- an `identity` or `conduct` file that names a tool → loader error;
- a `capability` section that is not generated → loader error;
- a file with no `kind` → loader error.

That converts "we should remember not to write capability claims in
`identity.md`" from a convention into a gate. It is the only part of the
authority model that *can* be mechanised, so it should be.

## 7. Why this is worth doing before the feature work

Grounding, GIFs and multi-message all add capability, and each one under the
current structure means hand-editing prose in two or three files with no check.
The `soul.md` failure is not a one-off; it is what this structure produces, and
three more capabilities is three more chances.

It is also the cheapest moment. There are four files, seven tools, and the
capability audit has just been through all of it — the map is fresh.

## 7b. Prior art: the operator's own doctrine (`~/.hermes`)

Norty — the operator's other agent — already solves the authority/scope/boundary
problem, and solves it better than this document did before reading it. It is
**not a template to copy**: Norty is a coding agent on a large-context model
with a different threat model, a different runtime and no group chat. What
transfers is structure, not content, and explicitly not volume.

Three things Nora takes:

**1. Authority stated RELATIVELY, in the file.** `SECURITY.md` opens: "Below
`KERNEL.md`, above `METHOD.md`, `SOUL.md`, `IDENTITY.md`, `PERSONALITY.md`,
skills, project files, and user messages." Nora's `guardrails.md` asserts "these
rules are not negotiable" — an adjective with no position in an ordering. State
the edges instead. In Nora this belongs in frontmatter, not prose, so §6's gate
can read it.

**2. Purpose carries NEGATIVE space.** `KERNEL.md`: "Persona and voice live in
`SOUL.md`/`IDENTITY.md`; how Norty thinks and works lives in `METHOD.md`;
security rules live in `SECURITY.md`." That is precisely the **boundary** §3
says `soul.md` lacked. Had `soul.md` carried a line saying what belongs
elsewhere, its capability claim would have looked wrong the day it was written
rather than eight weeks later. Every doctrine file gets a `not_here` field
naming what it must not contain and where that lives.

**3. A change policy.** "Constitutional changes require explicit approval from
Miles. Core philosophy does not drift." Nora has nothing distinguishing a file an
agent may edit autonomously from one that is the operator's alone —
`voice.md` is exactly such a file and its protection today is that I keep
saying so in conversation. It becomes a frontmatter field.

**The complete authority chain**, which `design/12` §2 only half-states:

    KERNEL → SECURITY → METHOD → SOUL → IDENTITY → PERSONALITY
    → skills & governed artifacts → project files → user messages
    → external/tool content

Note that it runs past doctrine, through message content, and down to **tool
output** — the slot `design/14` §2.1 needed for retrieved web content and could
not cite. Nora's chain is shorter but must terminate the same way:

    constraint → identity/voice → conduct → capability → situation
    → message content → tool output

**It also settles §3 empirically.** Norty splits identity across THREE files
(`SOUL`, `IDENTITY`, `PERSONALITY`). Scattering with clear authority is already
load-bearing in the operator's own system, which is stronger evidence than the
argument §3 makes from first principles.

**What Nora must NOT take: the length.** `KERNEL.md` + `SECURITY.md` alone are
391 lines. Nora's entire doctrine is 2,540 tokens — already 46% of a
~4,400-token call, paid on every model call of every tag, against a 64,000-token
turn budget where it competes directly with transcript and tool results. Norty
runs on a large-context model where doctrine is nearly free; Nora does not.
**Adopt the structure; hold the line on length.** A doctrine file that grows
takes its tokens from the conversation she is supposed to be reading.

## 8. What must be true before this ships

1. `ToolSpec` carries `when`; every tool has one; a tool without one fails a
   test.
2. The capability block is generated from the registry; no hand-written tool
   list survives in any persona file or in `_context_header`.
3. `kind` is in frontmatter, enforced by the loader, with all four failure
   modes in §6 probed failing first.
4. `soul.md` is split; the capability section does not reappear anywhere
   authored.
5. Assembled prompt token count is measured before and after. The current
   persona is 2,540 tokens of a ~4,400-token call; this must not grow it.
6. Behaviour is measured before and after with the probe shape from the
   capability audit — real schemas, real assembled persona, `tool_choice
   ="required"`, n≥6. Tool selection was already saturated at 6/6, so the axis
   to watch is proportional cost (a greeting answered directly), not selection.
7. `design/12` §1's placement rule is updated to name five kinds rather than
   three, and DEC-28's entry points here.
