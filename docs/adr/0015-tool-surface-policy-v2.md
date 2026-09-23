# 0015 — Tool names, descriptions and content: the policy, and which rule is checked how

- **Status:** accepted (operator, 2026-09-24)
- **Date:** 2026-09-24
- **Supersedes, in part:** [0007](0007-tool-surface-policy.md)
  §3.5 (English only, replaced by §3 below), §5 (the length ladder, now a
  declared field with numeric ceilings), and the clause of §3 that forbids a
  name sharing a sibling's token (narrowed in §1 below). 0007 §1, §2 and §4
  stand.
- **Extends:** [0004](0004-tool-naming-is-measured.md) with a rule for NEW
  names. 0004's "a rename is measured" is unchanged.
- **Un-parks:** `design/tool-catalogue/03-result-handling.md` (parked
  2026-09-22). The operator reopened it on 2026-09-23 ("research first, then
  design", then "go write the ADR") after the descriptions were measured.
- **Index:** DEC-199.

## Context

A tool's text is what the model reads to choose a tool, call it, and read its
result. 0007 set a policy for it on 2026-08-31.

**That policy was never met, including on the day it was written.** Measured
with cl100k over the description Strands actually sends (the docstring minus
its `Args:` section):

| | 0007's commit (`c4eba218`, 53 tools) | master today (56 tools) |
|---|---|---|
| total description tokens | 15,109 | 17,485 |
| descriptions under 80 tokens | 11 | 8 |
| 0007's own Level 1 examples | `weather` 305, `version` 315 | `weather` 313, `version` 327 |
| em dashes | 286 in 45 tools | 300 in 48 tools |
| CJK characters | 663 in 20 tools | 644 in 22 tools |

0007 named `weather` and `version` as Level 1 ("about two sentences") while
each ran to about 300 tokens. The dashes and CJK were already there. What held
was what had a test: the opening states when to call, and a MUST means turn
correctness. What had no test was not met on day one and was not met after.
AGENTS.md never pointed at 0007.

`design/tool-catalogue/03-result-handling.md` measured (on 54 tools, 2026-09-21)
that 8,602 tokens, then 54% of description text, told the model how to
interpret or report a result. Against today's 17,485 that share is about 49%.

**What published guidance says.** Sources and URLs are listed at the end;
where a source argues against a rule here, it is stated.

- **Where guidance agrees:**
  - A description says what a tool does and what its output is (Anthropic,
    OpenAI, Strands).
  - Allowed values belong in enums and types rather than prose (OpenAI,
    Gemini, Anthropic's examples).
  - Guidance on a result's meaning or next step can live in the returned
    payload (MCP `outputSchema` and error content, Anthropic's engineering
    post on error responses).
- **Where guidance disagrees with this ADR:**
  - **Length.** Anthropic's define-tools page says "Provide extremely
    detailed descriptions. This is by far the most important factor in tool
    performance… Aim for at least 3–4 sentences per tool description, more
    if the tool is complex."
  - **Where "when to use" goes.** OpenAI says to describe when and when not
    to use each function in the system prompt.
  - **Examples.** OpenAI says to "Include examples and edge cases".
  - **Adding back.** The MCP description-smells study (arXiv 2602.14878)
    found that removing the Examples component did not degrade performance.
    Its headline, though, is the other direction: adding the missing
    components raised task success by a median 5.85 points, with no single
    combination winning everywhere.
  - **EASYTOOL** (arXiv 2401.06201) cut tool docs by 70–97%, and accuracy
    rose (FuncQA error 9.38% → 2.34%). It did so with a unified template and
    its own prompting, so the gain is not conciseness alone.
- **Measured elsewhere, relevant here:** editing descriptions alone moved
  tool choice by more than 10× (arXiv 2505.18135). Every change here is
  therefore measured on this deployment, not inferred from any of the above.

So the evidence does not say "shorter is better". It says a description should
carry what the tool is, when to use it and what it returns, well enough to
choose, and that more is not free. The ceilings below are set at Anthropic's
floor (3–4 sentences, estimated at 60–80 tokens), not below it. They are starting
values, and the eval moves them.

**Strands' extraction.** strands 1.50.2 (`tools/decorator.py:233-282`) sends the
whole docstring except the `Args:` section; `Returns:` and `Notes:` are sent
too. (Its docs page says "the first paragraph", but the code sends the whole
docstring.) Counts in this ADR are over that extracted text.

**Names.**

- **Prefixes are the norm.** Anthropic (`asana_search`), OpenAI namespaces and
  MCP's dotted names group tools by service or domain.
- **Word order is unsettled.** Anthropic reports "non-trivial effects" from
  prefix versus suffix and tells each team to decide by its own evaluation.
- **Split versus unify is untested.** No source tests one tool with a
  `network` parameter against one tool per network.
- **This repo's own counter-evidence.** 0004 measured a name that shared a
  token with a sibling (`person_profile` beside `lookup_person`) at 0/16.

## Decision

### 1. Names

- **A new tool is named `<domain>_<verb>`**, using `[a-z0-9_]` and at most 64
  characters.
  - The domain is a service (`web`, `instagram`, `x`) or a resource (`note`,
    `room`, `music`).
  - The verb comes from a list kept as a documented constant in
    `tests/architecture/test_tool_names.py`: today `read` (a known subject),
    `fetch` (a URL), `search` (free text), `list`, `create`, `update`,
    `cancel`, `send`.
  - A new verb is added to that constant in a PR that states why, not by a new
    ADR.
- **Same-domain siblings may share the domain token.** This narrows 0007 §3.
  `instagram_read` and `instagram_search` compete only with each other; what
  0004 measured was a shared token across two different domains. Cross-domain
  overlap stays forbidden. A new same-domain pair is added to
  `evals/corpora/tool_selection.yaml` so its routing is measured, not assumed.
- **Planned names follow the rule before they are built.** The self-awareness
  bundle planned `my_rooms`, `read_room` and `room_activity`. On 2026-09-24
  its owner made them two tools, `room_list` (a room exists, and its activity:
  one scope, no message text) and `room_read` (its content, where other
  people's words start), in `design/self-awareness/03-room-tools.md`. Both fit
  the rule without a new verb.
- **Existing names stay.** A rename is measured one at a time (0004), and only
  when `tool_selection` shows the name causing a misroute.
- **Split or unify is decided by a `tool_selection` arm**, and the numbers are
  recorded in the PR.
- **Side effects are declared, not named**, on `NativeTool`: `read_only`,
  `side_effect_state`, `idempotency`, `retry` (0008).

### 2. Description: the first paragraph (L1)

- The docstring's first paragraph is the tool's catalogue line. It says what
  the tool does and when to call it. As a catalogue line
  (`- <name> — <paragraph>`), name included, it fits in 40 tokens (0007 §1).
- **New:** where the output is not obvious from what the tool does, the
  paragraph also says what the output is, in a few words ("returns the
  matching lines with who said them").

### 3. Content: everything after the first paragraph

- **Every tool declares its level** on `NativeTool`, as
  `level: int | None = None`, where `None` means "not yet declared". The ladder
  is 0007 §5's. The ceiling is counted over the extracted description (see
  Context):

  | level | what the content holds | ceiling |
  |---|---|---|
  | 1 | what + when + what it returns, 3–4 sentences | 80 tokens |
  | 2 | Level 1 plus one behaviour-changing constraint (a MUST or SHOULD) | 160 tokens |
  | 3 | Level 1 plus a contract: statuses or refusals she must act on differently, one line each | 320 tokens |

  The ceilings are starting values. Each moves only with a measurement,
  recorded beside the constant.
- **Result interpretation lives in the result.** How to read or report a
  status goes in the tool's own returned dict, in a `note` or a reason, and
  appears only when that status comes back. The `output_contract` seam
  (`nora/agent/ports.py`) checks required keys and leaves extra keys alone. It
  carries a `note`; it does not add one. The description names the statuses;
  the result explains them.
- **Pre-call writing guidance is not result interpretation.** `send_reply`'s
  SHOULD block (how to write the reply) is her voice contract (0007 §5a), not
  something that can move into a result. It is exempt from the ceiling, counted
  separately, and its destination (stays, or moves to operator-owned doctrine)
  is an operator decision. `send_reply` is not in the first batch.
- **Allowed values are types.** An argument that takes one of a fixed set
  (`what="online"`) is a `Literal` or enum in the signature, not a list in
  prose.
- **RFC 2119** as in 0007 §2.
- **English, and at most one non-English string.**
  - The unit is a distinct maximal run of non-Latin characters, counted once
    however often it repeats. `remember`'s 記低 and 記低咗 are two strings.
  - A description keeps only a string the tool fires on (a trigger).
  - `remember`'s «say 記低咗 only when it is saved» is an output rule, so it
    moves to the result's `note`.
  - Example utterances and example argument values (place names) are removed.
    OpenAI recommends examples, while the smells study found no loss without
    them, so this is a bet, and the first batch measures it.
- **No em dashes or en dashes** (`—`, `–`) in a description. Every dash in
  every call is a demonstration of a style her doctrine asks her not to use.
  Use a comma, a full stop, a colon or parentheses.
  - The catalogue renderer's own separator (`nora/agent/registry.py`) is a
    dash too. It is only in the prompt when `defer_tools` is on (off today);
    it changes to a colon before that flag ever does.

### 4. How a description is changed

1. **Write it** to §1–§3.
2. **Humanize it.** Run the `humanizer` skill (where the session has it; it
   lives outside the repo) over the first paragraph and the content. Its
   mechanical checks (dashes, CJK, length) are tests in §6, so a session
   without the skill still meets them.
3. **Gate it.** `make gate`.
4. **Measure it**, before and after, on the arm production runs:
   - **Routing:** `python -m evals tool_selection --sample 5 --reasons`, for a
     change to a first paragraph, a name or a level.
   - **Reporting:** a change that moves interpretation into a result changes
     what she SAYS, which routing cannot see. The first batch adds
     `tool_reporting` cases: a tool returns `unavailable`, `nothing_found` or
     a refusal, and the reply must report it as that. They are graded by the
     existing judge (`--judge`). Until that corpus exists, a batch that moves
     interpretation does not merge.
   - The numbers go in the PR.

### 5. Rollout

- **Allowlists.** The §3 tests would fail on most of the 56 tools today. Each
  carries an allowlist of tools not yet rewritten, pinned by length, so it
  can only shrink. A NEW tool is never on the list, so it meets the policy
  from its first commit.
- **The first batch** is `where_been`, `remind`, `where_is` and `online_now`:
  the longest descriptions after `send_reply`, and the ones with the most
  interpretation prose. Its routing and reporting measurements decide two
  things before the rest follow: whether interpretation reads better from the
  result, and whether removing examples costs anything.
- **Status edit.** 0007's status line was changed to "superseded in part by
  0015" in the commit that accepted this ADR.

### 6. Which rule is checked how

| rule | check | status |
|---|---|---|
| name charset, length | `tests/architecture/test_tool_names.py` | exists |
| new name is `<domain>_<verb>` | `tests/architecture/test_tool_names.py` | exists |
| same-domain pair measured | `tool_selection` arm in the PR | process |
| rename or split/unify measured | `tool_selection` arm in the PR | process (0004) |
| side effects declared | `tests/unit/test_tool_registry.py` read/act partition (0008) | exists |
| first paragraph is what + when, ≤40 tokens | `tests/test_tool_surface_policy.py::test_the_opening_paragraph_is_an_l1` | exists |
| first paragraph says what it returns | review | process |
| level declared | `test_every_tool_declares_a_level` | first batch |
| description within its level | `test_description_fits_its_level` (on the extracted text) | first batch |
| nothing after `Args:` is silently dropped | `test_no_text_after_args_is_lost` | first batch |
| a Level 3 status has a result `note` | `test_every_listed_status_returns_a_note` | first batch |
| fixed options are types | review | process |
| RFC 2119 register | existing `test_a_must_is_turn_correctness_not_style`, `test_style_guidance_is_labelled` | exists |
| at most one non-English string | `test_at_most_one_non_english_string` | first batch |
| no dashes | `test_no_dash_in_any_description` | first batch |
| humanized | the PR says it was run | process |
| routing and reporting measured | eval output in the PR | process |

## Consequences

- **Better.** One standard, pointed at from AGENTS.md and from `NATIVE_TOOLS`,
  with a stated check for every rule. Result interpretation paid only when a
  status fires: `03` measured −80% for `where_is` and −71% for `online_now`
  (on the 2026-09-21 tree).
- **Worse:**
  - **Shorter than some sources advise.** Descriptions sit at Anthropic's
    stated floor, and the examples OpenAI recommends are dropped. If those
    sources are right for this model, selection on complex tools gets worse.
    The first batch's measurement is what would show it.
  - **Every rewrite changes every turn.** Batches cost eval runs, routing and
    reporting both.
  - **Longer results when a status fires.** LongFuncEval measured accuracy
    loss as responses grow, so a `note` must stay short too.
  - **A level can be declared wrongly.** The ceiling catches a Level 1 that
    grew, not a Level 3 that should be a Level 1.
  - **Same-domain names are a bet.** They go against the repo's own measured
    failure (0004); the measured pair in `tool_selection` is the only guard.
- **Unmeasured:** whether reporting guidance works better in the result or in
  the prompt. No published source compares the two; the first batch does.

## Rejected

- **Editing 0007 in place.** Accepted ADRs are immutable in this repo.
- **Renaming every tool to the new pattern at once.** 0004 measured one
  overlapping name at 0/16. Renames stay one at a time, on evidence.
- **Longer descriptions, per Anthropic's "more if the tool is complex".**
  Deferred to the measurement. It is kept as the alternative the first batch
  can prove right.
- **A fixed multi-section template** (EASYTOOL-style). The ladder already sizes
  content by what the tool needs. A template would make every Level 1 carry
  empty sections.

## Sources

Fetched 2026-09-24.

- Anthropic, define tools: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- Anthropic, writing effective tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- OpenAI, function calling: https://developers.openai.com/api/docs/guides/function-calling
- MCP tools spec (annotations, outputSchema): https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Strands extraction: strands-agents 1.50.2, `strands/tools/decorator.py:233-282`
- EASYTOOL: https://arxiv.org/abs/2401.06201
- MCP tool descriptions are smelly: https://arxiv.org/abs/2602.14878
- Tool preferences in agentic LLMs are unreliable: https://arxiv.org/abs/2505.18135
- LongFuncEval: https://arxiv.org/abs/2505.10570
