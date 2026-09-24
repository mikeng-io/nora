# 0016 · Skills: when a domain earns one, and how a skill is written

- **Status:** proposed (2026-09-24)
- **Date:** 2026-09-24
- **Extends:** [design/12](../../design/12-prompt-tool-skill-boundaries.md) §1
  (placement) with a measured cost test, and §2 (authority) unchanged.
- **Sibling of:** [0015](0015-tool-surface-policy-v2.md), which covers tool
  text. This covers skill text, and borrows 0015's writing rules and process.
- **Replaces:** the 30 non-blank line ceiling in
  `tests/test_skill_content.py::test_every_skill_stays_small`.
- **Index:** a DEC number is taken at merge.

## Context

Nora has three skills in `nora/skills/`: `navigation`, `personalization` and
`image-generation`. Only a skill's name and description sit in the prompt;
the body arrives when she calls the `skills` tool (design/12 §1, progressive
disclosure). Until now the rules for skills lived in three places and covered
little:

- design/12 §1: needed every turn is prompt, a capability is a tool,
  "instructions for a domain that is usually irrelevant" is a skill.
- `tests/test_skill_content.py`: a description needs a "Not for" half, the
  skill names no scenario, and the body stays under 30 non-blank lines
  (image-generation is allowed 80). The only reason given for 30 is "a large
  skill is stealing a lower layer's job".
- The loader: `name` matches the directory, a `description` exists, and the
  authority frontmatter agrees.

Nothing governs how a skill is written (0015's rules cover tools only), and the
30 line ceiling was never measured. On 2026-09-24 the operator asked why a
skill of 30 lines is a skill at all rather than tool text, and what the
convention is. This ADR answers both, from measurement.

### What a skill costs, measured

Langfuse, tag path (`engagement` traces), 2026-09-01 to 2026-09-24:

| skill | body tokens (cl100k) | loads | turns in its domain | loaded in those turns |
|---|---|---|---|---|
| navigation | 445 | 2 | 6 (`route`, `transit`, parking tools) | 2 of 6 |
| personalization | 275 | 5 | not measurable from tool calls | n/a |
| image-generation | 1,449 | 4 (since it shipped 09-23) | 5 (`generate_image`) | 4 of 5 |

- 362 tag turns in the window; 12 loaded a skill (3.3%).
- A sample of 60 tag turns averaged 2.8 model calls (median 2) at about
  35.5k input tokens per call. The 12 skill-loading turns averaged 4.2 calls.
  A load therefore costs about 1.4 extra model calls, roughly 50k input
  tokens (mostly prefix-cached) and several seconds of latency.
- Doctrine text costs its tokens on every turn, but it is part of the cached
  prefix, so its marginal cost is the cached price
  (`tokens-are-not-cost-under-a-prefix-cache`).

Two findings follow:

1. **Size alone does not make a skill.** For a body of B tokens loaded on a
   fraction r of all tag turns, the doctrine is cheaper whenever
   B < r × 50k. Personalization (B = 275, r = 1.4%, threshold about 700) is
   cheaper as doctrine. Navigation (B = 445, r = 0.55%, threshold about 275)
   is marginally cheaper as a skill. Image-generation (B = 1,449) is a skill
   by any rate seen.
2. **A skill that is not loaded does not apply.** Navigation was loaded in 2
   of 6 turns that used its tools, so its judgement was absent from 4. The
   token test says nothing about that; only a reliability test does.

The numbers are small (n = 6 and 5 domain turns). They are enough to show the
question is measurable and that the line ceiling measures neither thing.

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

Nora's current descriptions give the when and the not-for, but not the what,
and are written to her in the second person.

## Decision

### 1. Placement: a skill must pass a cost test and a reliability test

- Text needed on every turn MUST go in the doctrine. A capability MUST be a
  tool. A rule about calling one tool MUST go in that tool's description
  (0015). Only judgement that spans several tools, in a domain most turns do
  not touch, MAY be a skill.
- **Cost test.** A skill of B body tokens, loaded on a fraction r of tag
  turns, SHOULD be moved to the doctrine when B < r × C, where C is the
  measured cost of one load in input tokens (50k on 2026-09-24). C and r are
  re-read from Langfuse, never assumed.
- **Reliability test.** In the turns that use a skill's tools or domain, the
  skill MUST be loaded in at least 80% of them. Below that, the description is
  fixed first (it is what triggers loading) and re-measured. If it still
  fails, a body under 1,000 tokens moves to the doctrine; a larger one is split
  into a short rule in the doctrine that is always present, plus the skill.
- Both numbers, with the window and n, go in the PR that adds, moves or
  changes a skill.

### 2. Name

- A skill is named with a noun phrase for its domain, in lowercase words
  joined by single hyphens, at most 64 characters, matching its directory
  (`navigation`, `image-generation`). The loader already enforces the match.

### 3. Description: what, when, and not for

- The description is the only part in every prompt. It MUST say, in this
  order: what the skill covers, when to load it, and what it is not for.
- It MUST fit in 80 tokens, measured as the loader renders it.
- It MUST NOT name a scenario (a particular trip, meal or occasion); the
  per-skill scenario lists in `tests/test_skill_content.py` stay.
- It is addressed to Nora, in the second person. This departs from
  Anthropic's third person advice on purpose: every other line in her prompt
  speaks to her, and a single third person voice would be the inconsistency
  that advice is trying to avoid.

### 4. Body

- Judgement only. The body MUST NOT describe how a tool works (that is the
  tool's description) or restate the doctrine (it may point to it).
- One rule per section, and the heading states the rule.
- **A token ceiling replaces the line ceiling.** A body SHOULD stay under
  1,500 tokens. A skill above that MUST carry its reason in
  `tests/test_skill_content.py`, where the number is, as image-generation's
  raised ceiling does today. The published ceiling (5,000) is the hard limit.
- A skill is used by one model family in this deployment, so it is tested on
  that family (the deployed alias), not on several.

### 5. Writing

The tool rules from 0015 apply to skill text too:

- RFC 2119 words for anything required; plain prose for judgement.
- English, except quoted speech and at most one non-English trigger word.
- No em or en dashes.
- Every new or rewritten skill goes through the `humanizer` pass.
- A rewrite MUST be audited by a reviewer told to find rules it lost. The
  first rewrite under this policy (navigation and personalization,
  2026-09-24) lost three rules and softened three more before that audit.

### 6. Authority

Unchanged from design/12 §2: a skill ranks below the doctrine and above
message content, MAY add procedure and MUST NOT relax a rule above it, keeps
`change_policy: operator-only`, and MUST NOT be loadable because a message
names it.

### 7. How a skill is changed

1. Measure first: the load rate, the domain turns and whether it loaded in
   them, from Langfuse.
2. Write, humanize, audit for lost rules, `make gate`.
3. Measure after: a `skill_selection` corpus (to be added, like
   `tool_selection`) with rows where the skill must load and rows where it
   must not; plus the behaviour cases the skill exists for.
4. Numbers in the PR.

### 8. Rollout

1. Add the checks in §9 marked "to add".
2. Re-place `personalization` by the cost test: its 275 tokens move to
   conduct.md next to the principle it points at, unless the reliability
   measurement argues otherwise.
3. Fix `navigation`'s description (it misses the "what") and re-measure its
   load rate in route and transit turns before deciding its placement.
4. `image-generation` is owned by the sessions that last changed it (#456,
   #469); it passes both tests on the numbers above and is left to them.

### 9. Which rule is checked how

| rule | check | status |
|---|---|---|
| name matches directory | `nora/skills/loader.py` | exists |
| description has a not-for half | `test_every_description_says_both_when_to_load_and_when_not_to` | exists |
| description says what the skill covers | review | process |
| description within 80 tokens | `test_every_description_fits` | to add |
| no named scenario | `test_every_skill_names_no_scenario` | exists |
| body within its token ceiling | `test_every_skill_stays_small`, counting tokens | to change |
| no dashes | `test_no_dash_in_any_skill` | to add |
| humanized, audited | the PR says so | process |
| cost and reliability tests | Langfuse numbers in the PR | process |
| loads when it should, not when it should not | `skill_selection` corpus | to add |
| authority frontmatter agrees | `nora/skills/loader.py` | exists |

## Consequences

- **Better.** Placement is decided by what a skill costs and whether it
  reaches the turns it is for, not by its length. A short skill that is
  cheaper as doctrine gets moved, and a skill that fails to load gets its
  trigger fixed instead of its body lengthened.
- **Worse:**
  - **More measurement per change.** Every skill change now needs a Langfuse
    read and, once it exists, a `skill_selection` run.
  - **The thresholds rest on small numbers.** 80% reliability and 1,500
    tokens are judgement calls; the domain counts behind this ADR are 6 and 5.
  - **Moving a skill to the doctrine grows every prompt.** It is cheap because
    it is cached, but a doctrine change busts the cache once and changes what
    every turn reads, which a skill change does not.
  - **"Turns in its domain" is a proxy.** It counts turns that used the
    skill's tools, which misses turns where she should have planned and used
    no tool at all.

## Rejected

- **Keeping the 30 line ceiling.** It measures neither cost nor reach, and
  lines are not tokens.
- **Putting cross-tool judgement in tool descriptions.** It would be copied
  into several tools, or read only after a tool was already chosen, and 0015
  keeps descriptions to what, when and pre-call rules.
- **Moving every skill to the doctrine.** Image-generation is 1,449 tokens
  used on a small share of turns; carrying it everywhere costs more than
  loading it.
- **Third person descriptions.** See §3.

## Sources

Fetched 2026-09-24.

- Agent Skills specification: https://agentskills.io/specification
- Anthropic, skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Strands AgentSkills plugin: https://strandsagents.com/docs/api/python/strands.vended_plugins.skills.agent_skills/
- design/12-prompt-tool-skill-boundaries.md §1, §2, §4
- Measurement scripts: Langfuse public API, `skills` tool observations and
  `engagement` traces, 2026-09-01 to 2026-09-24 (run from norty).
