# 0016 · Skills: when a domain earns one, and how a skill is written

- **Status:** proposed (2026-09-24)
- **Date:** 2026-09-24
- **Extends:** [design/12](../../design/12-prompt-tool-skill-boundaries.md) §1
  (placement) with a cost test and a reach test. §2 (authority) is unchanged,
  except that its line calling item 5 "an empty rank" has been stale since
  DEC-48 shipped the first skill.
- **Sibling of:** [0015](0015-tool-surface-policy-v2.md), which covers tool
  text. This covers skill text, and borrows 0015's writing rules and process.
- **Replaces:** the line ceilings in
  `tests/test_skill_content.py::test_every_skill_stays_small` (30 by default,
  from DEC-48; 80 for image-generation, from DEC-193).
- **Index:** a DEC number is taken at merge.

## Context

Nora's skills live in `nora/skills/`. Only a skill's name and description sit
in the prompt, rendered by the Strands `AgentSkills` plugin
(`nora/agent/factory.py`); the body arrives when she calls the `skills` tool
(design/12 §1, progressive disclosure). Until now the rules for skills lived
in three places and covered little:

- design/12 §1: needed every turn is prompt, a capability is a tool,
  "instructions for a domain that is usually irrelevant" is a skill.
- `tests/test_skill_content.py`: a description needs a "Not for" half, the
  skill names no scenario, and the body stays under 30 non-blank lines
  (image-generation, 80). DEC-48 gave the reason for keeping a skill small:
  what looks like skill content usually belongs a layer up or down.
- `nora/skills/loader.py`: `name` matches the directory and a `description`
  exists. The `authority_above` and `authority_below` keys a skill carries are
  passed through and never checked; the edge check in
  `nora/doctrine/loader.py` covers doctrine sections only.

Nothing governs how a skill is written (0015 covers tools only), and the line
ceilings measure neither what a skill costs nor whether it reaches the turns
it is for. On 2026-09-24 the operator asked why a skill of 30 lines is a skill
at all, and what the convention is.

### What a skill costs, measured

Langfuse, tag path (`engagement` traces), 2026-09-01 to 2026-09-24. Loads are
`skills` tool observations; domain turns are traces with a call to the named
tools.

| skill | body tokens (cl100k) | loads | turns that used its tools | loaded in those |
|---|---|---|---|---|
| navigation | 445 | 2 | 6 (`route`, `transit`, parking tools) | 2 |
| personalization | 275 | 5 | not measurable from tool calls | n/a |
| image-generation | 1,449 | 4 (it shipped 09-23) | 5 (`generate_image`) | 4 |

- 362 tag turns in the window; 12 loaded a skill (3.3%).
- A sample of 60 tag turns averaged k = 2.8 model calls (median 2) at about
  35.5k input tokens per call. The 12 skill-loading turns averaged 4.2 calls,
  but that gap is not the load's cost: planning, recommending and drawing
  turns use several tools whether or not a skill loads. Structurally, a load
  is one model call (design/12 §4), so one load costs about C = 35.5k cached
  tokens plus the body.
- Doctrine text is part of the cached prefix and is re-read on every call,
  so B tokens of doctrine cost B × k cached tokens per tag turn, and it is
  also paid on proactive fires (`nora/app/fire.py` reads the same doctrine),
  which a tag-path load rate never sees.

Two findings follow:

1. **Cost barely separates the options at this size.** Personalization as
   doctrine is 275 × 2.8 ≈ 770 cached tokens per tag turn; as a skill,
   1.4% × 37k ≈ 520. Both are small, and 5 loads cannot decide between them.
2. **Reach separates them.** Navigation loaded in 2 of the 6 turns that used
   its tools. Its own description says a single live check does not need it,
   so some of those 4 were correct non-loads, and the number is a monitor,
   not a verdict. It is still the question that matters: a skill that is not
   loaded does not apply, and a principle other skills need (personalization
   reaches plans and pictures, not only suggestions) would need a second
   load to apply there.

### Published convention

The Agent Skills specification (agentskills.io) and Anthropic's authoring
guide agree on:

- `name`: 1 to 64 characters, lowercase letters, digits and single hyphens,
  matching the directory.
- `description`: 1 to 1,024 characters, saying **what the skill does and when
  to use it**, with the words a request would contain. Anthropic adds: third
  person, because it is injected into the system prompt.
- Progressive disclosure: metadata about 100 tokens, body under 5,000 tokens
  recommended (under 500 lines), references one level deep.
- Evaluations first: measure the task without the skill, write the least text
  that fixes the failures, and test that the skill loads when expected.
  Anthropic also advises testing on every model the skill will run on.

Nora's descriptions gave the when and the not-for, but not the what.

## Decision

### 1. Placement: reach first, then cost

- Text needed on every turn goes in the doctrine. A capability is a tool. A
  rule about calling one tool goes in that tool's description (0015). A skill
  is judgement for a domain most turns do not touch that no single tool
  description can hold within 0015's ceilings.
- **Judgement that other domains also need is doctrine, not a skill.** A
  skill cannot rely on another skill being loaded in the same turn.
- **Reach test.** A skill SHOULD load in the turns labelled "should load".
  The label comes from `tool_selection` rows that name `skills`
  (`tools: [skills]` where it must load, `not_tools: [skills]` where it must
  not); the Langfuse domain count is a monitor. The starting bar is 80% of
  the should-load rows, and the eval moves it; nothing is decided on fewer
  than 20 labelled turns.
- **When a skill fails the reach test,** its description is fixed first, since
  the description is what triggers loading, and re-measured. If it still
  fails, **reach outranks cost**: judgement that is not read is the larger
  loss. The rule that must always hold moves to the doctrine; what is left
  stays a skill only if it passes the cost test.
- **Cost test.** A skill of B body tokens, loaded on a fraction r of tag
  turns, is cheaper as doctrine when B × k < r × C, with k the mean model
  calls per tag turn and C the input tokens of one load call. Both sides are
  cached-token costs, so the price cancels. k, r and C are read from Langfuse
  for each decision, never carried over. Latency is not in the test; a load
  adds one model round trip, and the PR says so when it matters.
- The numbers, with the window and n, go in the PR that adds, moves or
  changes a skill.

### 2. Name

- A noun phrase for the domain, in lowercase words joined by single hyphens,
  at most 64 characters, matching its directory (`navigation`,
  `image-generation`). The loader enforces the match.

### 3. Description: what, when, and not for

- The description is the only part of a skill in every prompt. It says, in
  this order, what the skill covers, when to load it, and what it is not for.
- It MUST fit in 80 cl100k tokens, counted on the raw `description` string.
- It MUST NOT name a scenario (a particular trip, meal or occasion); the
  per-skill lists in `tests/test_skill_content.py` stay.
- It speaks to Nora in the second person. This departs from Anthropic's third
  person advice on purpose: every other line of her prompt speaks to her, and
  a lone third person voice would be the inconsistency that advice guards
  against.

### 4. Body

- Judgement only. It does not describe how a tool works (that is the tool's
  description) and does not restate the doctrine; it may point to it.
- One rule per section, and the heading states the rule.
- **Token ceilings replace line ceilings.** A body SHOULD stay under 1,500
  cl100k tokens. A skill above that carries its own ceiling and reason in
  `tests/test_skill_content.py`, where the number is. 5,000, the published
  limit, is the hard limit.
- It is tested on the deployed alias. Anthropic's advice to test on every
  model applies to a skill shipped to several; this deployment runs one.

### 5. Writing

0015's writing rules apply to skill text:

- RFC 2119 words only for what is required; plain prose for judgement.
- English, except quoted speech and at most one non-English trigger word.
- No em or en dashes.
- Every new or rewritten skill goes through the `humanizer` pass.
- A rewrite is audited by a reviewer told to find the rules it lost. The
  first rewrite under this policy (navigation and personalization,
  2026-09-24) had lost three rules and softened three more before that audit.

### 6. Authority

Unchanged from design/12 §2: a skill ranks below the doctrine and above
message content, may add procedure and MUST NOT relax a rule above it, keeps
`change_policy: operator-only`, and MUST NOT load because a message names it
(R-29).

### 7. How a skill is changed

1. Measure first: loads, the turns that used its tools, and whether it loaded
   in them, from Langfuse.
2. Write, humanize, audit for lost rules, `make gate`.
3. Measure after: the `skills` rows in `tool_selection`, and the behaviour
   cases the skill exists for.
4. Numbers in the PR.

### 8. Rollout (2026-09-24, in the PR that proposes this ADR)

1. **Personalization moved into `conduct.md` (v32)** and the skill was
   retired: it is a principle that plans and pictures need too, and cost did
   not separate the options (finding 1). The precedent is `self-description`
   into `identity.md` on 2026-09-17.
2. **Navigation redesigned.** Its description now says what it covers. Its
   body adds where each person starts: close to leaving, where they are now;
   well before, where they usually are at that hour, said as usual; what they
   say about themselves above both; personal places by their saved names. It
   points to conduct's personalization rule for whose plan it is. Every
   earlier rule is kept.
3. **Checks added:** token ceilings, the 80 token description, no dashes.
4. **To do:** `skills` rows in `tool_selection` for the reach test, and a
   check that a skill's authority keys agree.
5. **image-generation is left unchanged;** its next change applies this ADR.
   It passes both tests on the numbers above and keeps a 2,000 token ceiling
   (measured 1,449), which leaves room for the one section per configured
   model it is meant to grow by.

### 9. Which rule is checked how

| rule | check | status |
|---|---|---|
| name matches directory | `nora/skills/loader.py` | exists |
| description has a not-for half | `test_every_description_says_both_when_to_load_and_when_not_to` | exists |
| description says what the skill covers | review | process |
| description within 80 tokens | `test_every_description_fits` | exists |
| second person description | review | process |
| no named scenario | `test_every_skill_names_no_scenario` | exists |
| body within its token ceiling | `test_every_skill_stays_small` | exists |
| judgement only, one rule per section | review | process |
| RFC 2119 register, English | review | process |
| no dashes | `test_no_dash_in_any_skill` | exists |
| humanized, audited for lost rules | the PR says so | process |
| reach test | `skills` rows in `tool_selection` | to add |
| cost test | Langfuse numbers in the PR | process |
| tested on the deployed alias | eval output in the PR | process |
| a message cannot load a skill (R-29) | `tests/test_skill_loader.py::test_a_message_can_name_a_skill_but_never_load_one` | exists |
| authority keys agree | none; the skill loader does not read them | to add |

## Consequences

- **Better.** Placement is decided by whether a skill reaches the turns it is
  for, then by what it costs, not by its length. A principle that several
  domains need lives where all of them read it.
- **Worse:**
  - **More measurement per change.** Every skill change needs a Langfuse read
    and, once the rows exist, a `tool_selection` run.
  - **The bars rest on small numbers.** 80% and 1,500 tokens are starting
    values; the domain counts behind this ADR are 6 and 5.
  - **The doctrine grows.** `conduct.md` carries the personalization ladder on
    every call of every turn and every proactive fire, about 770 cached tokens
    per tag turn, and a doctrine change busts the prefix cache once.
  - **The Langfuse monitor is a proxy.** It counts turns that used a skill's
    tools, which includes turns that correctly did not load it and misses turns
    that should have planned and used no tool.

## Rejected

- **Keeping the line ceilings.** They measure neither cost nor reach, and
  lines are not tokens.
- **Putting cross-tool judgement in tool descriptions.** It would be copied
  into several tools or read only after a tool was chosen, and 0015 keeps
  descriptions to what, when and pre-call rules.
- **Moving every skill to the doctrine.** image-generation is 1,449 tokens used
  on a small share of turns.
- **A separate `skill_selection` corpus.** `tool_selection` already grades
  executed tools, and `skills` is one.
- **Third person descriptions.** See §3.

## Sources

Fetched 2026-09-24.

- Agent Skills specification: https://agentskills.io/specification
- Anthropic, skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Strands AgentSkills plugin: https://strandsagents.com/docs/api/python/strands.vended_plugins.skills.agent_skills/
- design/12-prompt-tool-skill-boundaries.md §1, §2, §4; DEC-48, DEC-50, DEC-193
- Measurement: the Langfuse public API on norty. Loads from
  `GET /api/public/observations?name=skills`; domain turns from the same query
  for `route`, `transit`, `find_parking`, `parking_status` and
  `generate_image`; turns from `GET /api/public/traces?name=engagement`;
  calls per turn from the `chat` observations under `invoke_agent nora` in 60
  traces since 2026-09-10.
