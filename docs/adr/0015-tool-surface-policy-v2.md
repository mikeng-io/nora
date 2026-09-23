# 0015 — Tool names, descriptions and content: the policy, with a gate per rule

- **Status:** proposed
- **Date:** 2026-09-24
- **Supersedes, in part:** [0007](0007-tool-surface-policy.md) §3.5 (English
  only, replaced by the tighter rule below) and §5 (the length ladder,
  now declared and gated). 0007 §1, §2, §3 and §4 stand.
- **Extends:** [0004](0004-tool-naming-is-measured.md) with a rule for NEW
  names; 0004's "a rename is measured" is unchanged.
- **Index:** DEC number taken at merge. Research, measurements and arms:
  `design/tool-catalogue/05-measurement.md` §3a; operator direction
  2026-09-23/24.

## Context

A tool's text is what the model reads to choose a tool, call it, and read its
result. 0007 set a policy for that text on 2026-08-31. Three weeks later,
measured on master (cl100k, 56 tools):

| what 0007 said | what the tree holds |
|---|---|
| "Most tools are Level 1", about two sentences | 8 of 56 descriptions are under 80 tokens. 0007's own Level 1 examples: `weather` is 313 tokens, `version` 327. |
| "every tool declares which level it is (the enforcement test derives it)" | no tool declares a level, and no such test was written |
| English only, quoted examples allowed as an edge case | 644 CJK characters in 22 tools, nearly all examples |
| nothing on punctuation | 300 em dashes in 48 of 56 tools |

The descriptions total 17,485 tokens. Measured in
`design/tool-catalogue/03-result-handling.md`, 54% of that text tells the model
how to interpret or report a result (what `unavailable` means, what may be
said aloud). It is read only after a call returns, and every call pays for it.

**Why the policy did not hold.** Its rules that had a test held: the opening
states when to call, and a MUST means turn correctness. The rules without a
test drifted from the first tool added after them. AGENTS.md never pointed at
0007, and the sessions that added tools did not read it.

**What published guidance says** (sources and URLs under "Sources" below).
Anthropic, OpenAI, Gemini, MCP and Strands agree on three things:

1. A description says what the tool does, when to use it, and what its output
   represents.
2. Guidance on reporting a result belongs in the result payload (MCP, Anthropic
   engineering, Gemini) or in the prompt (OpenAI GPT-5.1), not in the
   description.
3. Allowed values belong in enums and types, not in prose.

The evidence also shows:

- **Shorter descriptions were also more accurate.** EASYTOOL condensed tool
  docs by 70–97% and accuracy rose (FuncQA error 9.4% → 2.3%).
- **Examples did not help.** Removing the Examples component made no
  significant difference (MCP tool-description smells, arXiv 2602.14878).
- **Wording alone moves tool choice.** Edited descriptions got over 10× more
  usage (arXiv 2505.18135), so every change here is measured, not assumed.
- **Strands copies the whole docstring minus `Args:` into the description**,
  so a `Returns:` or `Notes:` block costs description tokens on every call.

**Tool names.**

- **Service or domain prefixes are the norm.** Anthropic (`asana_search`),
  OpenAI namespaces and MCP's dotted names all group tools by service or
  domain.
- **Word order is undecided.** No source settles `verb_object` against
  `object_verb`. Anthropic reports "non-trivial effects" and tells each team to
  decide with its own evaluations.
- **Nobody compares split against unified.** No source tests a unified tool
  with a `network` parameter against one tool per network.

## Decision

Every rule below has a named test. A rule without one is not in this ADR.

### 1. Names

- **A new tool is named `<domain>_<verb>`.** The domain is the service
  (`web`, `instagram`, `x`) or the resource (`note`, `music`), and the name uses
  only `[a-z0-9_]`, at most 64 characters. The verb follows what the tool takes:
  - `_read` takes a known subject (a person, a note, a named thing);
  - `_fetch` takes a URL;
  - `_search` takes free text, and is always its own tool;
  - `_list` enumerates;
  - `_create` / `_update` / `_cancel` / `_send` act.
- **Existing names stay.** A rename is measured one at a time (0004) and is
  done only when `tool_selection` shows the name causing a misroute.
- **Split or unify by measurement.** One tool per network (`instagram_read`,
  `x_read`) or one tool with a `network` parameter is decided by a
  `tool_selection` arm, and the numbers are recorded in the PR.
- **Side effects are declared, not named.** Whether a tool writes, sends or
  changes state is declared on `NativeTool` (`read_only`, `side_effect_state`,
  `idempotency`, `retry`, as in 0008). No name marker carries it.
- Test: `tests/architecture/test_tool_names.py` checks the
  character set and length, and requires the `<domain>_<verb>` shape for any
  name not on a frozen list of today's names.

### 2. Description: the first paragraph (L1)

- The docstring's first paragraph is the tool's catalogue line. It says what
  the tool does and when to call it, and fits in 40 tokens. (0007 §1; gated
  since 2026-09-23 by `test_the_opening_paragraph_is_an_l1`.)
- **New:** where the output is not obvious from what the tool does, the first
  paragraph also says what the output represents, in a few words ("returns the
  matching lines with who said them"). It still fits in 40 tokens.

### 3. Content: everything after the first paragraph

- **Every tool declares its level** on `NativeTool` (`level=1|2|3`). The ladder
  is 0007 §5's. Its ceilings are now numbers, counted over the whole
  description Strands sends, which is everything except `Args:`:

  | level | what the content holds | ceiling |
  |---|---|---|
  | 1 | nothing past the first paragraph | 80 tokens |
  | 2 | one behaviour-changing constraint (a MUST/SHOULD), one short paragraph | 160 tokens |
  | 3 | a contract: distinct statuses or refusals the model must act on differently, one line each | 320 tokens |

  These are starting values. They move only with a `tool_selection`
  measurement, recorded beside the constant.
- **Result interpretation lives in the result.** How to read or report a
  status belongs in the returned payload, in its `note` or reason, and appears
  only when that status comes back. It goes through the `output_contract` seam
  (0008/0009). The description names the statuses; it does not explain them.
- **Allowed values are types.** An argument that takes one of a fixed set
  (`what="online"`) is a `Literal` or enum in the signature, not a list in
  prose.
- **RFC 2119** as in 0007 §2: MUST is only for turn correctness, SHOULD is
  style and carries a label, MAY is permission.
- **English, and at most one non-English token.** Normative prose is English.
  A tool may quote at most one non-English token, and only one the tool fires
  on (`remember`'s 記低). Other examples are removed; the evidence above found
  they made no significant difference.
- **No em dashes or en dashes** (`—`, `–`) in any description. Each one in
  every call is a demonstration, and demonstration beats instruction: the
  doctrine carries one rule against em dashes, while her input carries dozens.
  Use a comma, a full stop, a colon or parentheses.
- Test: `tests/test_tool_surface_policy.py` gains
  `test_every_tool_declares_a_level`, `test_description_fits_its_level`,
  `test_no_dash_in_any_description` and `test_at_most_one_non_english_token`.

### 4. How a description is changed

Every added or rewritten description goes through, in order:

1. **Write it** to §1–§3.
2. **Humanize it.** Run the `humanizer` skill over both the first paragraph
   and the content. A text we have not humanized is not one we can ask her to
   write like.
3. **Gate it.** `make gate`, which runs every test named above.
4. **Measure it.** A change to a first paragraph, a name, a level or a
   contract runs `python -m evals tool_selection --sample 5 --reasons`, on the
   arm that production runs, before and after. The numbers go in the PR. This
   is 0007 §3's ISO probe, with a corpus that exists now.

The humanizer step and the measurement are process rules. What a script can
check (length, dashes, CJK, level, names) is a test; what it cannot (the
reading, the routing) is the humanizer pass and the eval, and the PR shows
both.

### 5. Rollout

The tests in §1 and §3 would fail on most of today's 56 tools, so they cannot
switch on in one commit. Each carries an allowlist of the tools not yet
rewritten. The allowlist may only shrink, which is how
`tests/architecture/test_port_fakes_live_in_nora_testing.py` already works.
Every batch of rewrites removes its tools from the list in the same commit.
A NEW tool is never on the list, so it meets the policy from its first
commit. The first batch is the longest descriptions (`send_reply`,
`where_been`, `remind`, `where_is`, `online_now`), because they carry the
most interpretation prose and the highest risk. Its measurement decides
whether interpretation goes in the result or the prompt before the rest
follow.

## Consequences

- **Better:** one written standard, each rule a test, pointed at from
  AGENTS.md so a session adding a tool meets it before writing one. Expected to
  cut most of the 17,485 description tokens: `design/tool-catalogue/03`
  measured −80% for `where_is` and −71% for `online_now` with result handling
  moved to the result.
- **Worse:**
  - A rewrite of 56 descriptions is a behaviour change to every turn. Each
    batch has to be measured, not trusted, and that costs eval runs.
  - Moving interpretation into results makes those results longer when the
    status fires. LongFuncEval measured accuracy loss as responses grow, so
    the `note` text has to stay short too.
  - A declared level can be declared wrongly. The ceiling catches a Level 1
    that grew, but not a Level 3 that should be a Level 1.
- **Unmeasured:** whether reporting guidance works better in the result or in
  the prompt. No published source compares the two. The first batch measures
  it on `tool_selection` before the rest follow.

## Rejected

- **Editing 0007 in place.** Accepted ADRs are immutable in this repo.
- **Renaming every tool to the new pattern at once.** 0004 measured one
  overlapping name at 0/16. Renames stay one at a time, and only on evidence.
- **Keeping examples for the room's language.** The evidence found no
  significant effect from examples. The one quoted token a tool fires on is
  kept.
- **A fixed multi-section template for every description** (EASYTOOL-style).
  The ladder already sizes content by what the tool needs. A template would
  make every Level 1 carry empty sections.

## Sources

Fetched 2026-09-24. Quotes and numbers were taken from these pages. Anything
not checked against a primary source is marked UNVERIFIED in the research
report.

- Anthropic, define tools: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- Anthropic, writing effective tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, tool search: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- OpenAI, function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI, GPT-5.1 prompting guide (`<tool_usage_rules>`): https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-1_prompting_guide
- Gemini, function calling: https://ai.google.dev/gemini-api/docs/generate-content/function-calling
- MCP tools spec (annotations, outputSchema): https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- Strands, custom tools: https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools
- EASYTOOL: https://arxiv.org/abs/2401.06201
- MCP tool descriptions are smelly: https://arxiv.org/abs/2602.14878
- Tool preferences in agentic LLMs are unreliable: https://arxiv.org/abs/2505.18135
- LongFuncEval: https://arxiv.org/abs/2505.10570
