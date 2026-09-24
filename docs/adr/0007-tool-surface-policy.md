# 0007 — A tool's name and description are its routing surface: policy

- **Status:** accepted; §3.5, §5 and the sibling-token clause of §3 superseded by [0015](0015-tool-surface-policy-v2.md)
- **Date:** 2026-08-31
- **Index:** DEC-39 / ADR 0004 (naming is measured), DEC-41 (docstring
  selection regression, measured not argued), DEC-92 (the schema is the
  surface the model matches on), operator 2026-08-31 (English-only
  descriptions; three-level length ladder)

## Context

**Measured 2026-08-31, production-shape eval plus an ISO probe.** The
journeys suite (`n=9`, real production seed, skills enabled, three efforts)
counted «recovered a reply the model wrote WITHOUT calling send_reply»:
`none` 55 events, `low` **99**, `high` **99**. Directionality — reasoning
makes it *worse* — was the signal that this was not a harness, transport or
budget problem: it is the model reading the tool surface and deciding not to
call.

An ISO probe isolated the variable. Same prompt, same model
(`ollamacloud/deepseek-v4-flash`), same `tool_choice="required"`, `n=5`,
one description variant per run:

| variant | none | low | high |
|---|---|---|---|
| A — current send_reply description | 5/5 tool_calls | **0/5 prose** | **0/5 prose** |
| B — mandatory-primary-path opening | 5/5 | **5/5** | **5/5** |
| C — zero-ambiguity opening | 5/5 | 5/5 | 4/5 |

Changing ONLY the description text moved `low`/`high` from 0/5 to 5/5.
`tool_choice="required"` was honoured in the probe — the provider was not
ignoring it here — which rules out the transport-layer explanation for this
particular run. The description itself is the causal variable.

**Why current-description fails under reasoning.** The current `send_reply`
docstring (≈3,960 chars, the longest tool surface in the registry) opens with
what the tool IS, then spends most of its length on HOW to write well: «Read
`text` back before you send it, and fix these: Too long. A verdict that is
only a scoreboard. A tail that offers more. Caving. Too soft.» A reasoning
model reads those as preconditions on *deserving* to call: if my draft is too
long / too soft / a scoreboard, I should not send. `none` does not perform
that inference — it pattern-matches «there is a tool, call it» — which is
exactly the observed 55-vs-99 split. The style guidance is real, valuable
voice control, and it is positioned as if it were permission-to-call.

**The registry already has half the discipline.** `NATIVE_TOOLS` declares
budget (DEC-10), terminality (DEC-32), time axis (DEC-14), source tier
(E1/DEC-77) per tool — all machine-verified. Naming is covered by ADR 0004:
measured, one at a time, never argued from taste. **What has no policy is the
description itself** — the text Strands derives into `inputSchema` and sends
the model on every call. Existing tests pin *content* («One or two sentences
answers almost everything» is a literal assertion) — content that this probe
shows is load-bearing in the wrong direction. A description policy must
supersede content pinnings and state what the opening must do.

## Decision

**A tool's model-facing surface is `name + opening + normative load`, each
with a required shape, enforced by tests.**

### 1. The opening — what + when-to-call, never style

Every tool description's first sentence MUST state, in order:

1. **what** the tool does (one clause), then
2. **when to call it** (a when-to-call clause: «Call this when …»,
   «Use when …», «Call this for …», «Somebody tells you …», «the moment
   somebody asks …» — the trigger framing, in the tool's own register).

This is already the pattern of the healthy tools (`weather`: «The weather in
Hong Kong — current conditions… Call this for anything…»; `about`: «What you
know about someone… Call this when…»; `remember`: «Record a settled fact…
Somebody tells you something about themselves…»). The head is allowed to be
short — `version` («Which version of you is running… Call this the moment
somebody asks about YOU as software») is 2 clauses and a model-facing
trigger, not an essay. Style guidance, voice control, and negative examples
MUST come AFTER the opening, in a clearly separate block, and MUST NOT be
phrased as conditions on calling.

### 2. Normative load follows RFC 2119 — and permission is never a MUST

Tool guidance to the model is three registers, and mixing them is the defect
measured above:

| register | keyword | means | example |
|---|---|---|---|
| obligation | MUST | the call is mandatory; failing it is a turn error | `send_reply`: «You MUST call this tool exactly once whenever you produce a reply» |
| conduct | SHOULD | how to do it well; a breach degrades quality, not correctness | «Keep it one or two sentences» |
| permission | MAY | optional; never required | «You MAY call it several times for a burst» |

Rules:

- A MUST is only for behaviour the turn depends on. «MUST NOT return the
  reply as the assistant's final message» is a valid MUST (turn correctness);
  «MUST NOT be too long» is a SHOULD wearing a MUST costume (style).
- Permission MAY NOT be phrased as an obligation, and the reverse — a
  SHOULD is not permission to skip — does not exist. The probe's mechanism
  is exactly this swap: advisory style read as permission-to-omit.
- Style guidance belongs to SHOULD. If a tool's docstring is majority SHOULD
  with the MUST buried, the MUST goes first and the SHOULD block is
  explicitly labelled.

### 3. Routing-surface hygiene (ADR 0004's rules, stated as policy)

- A name is a routing feature; it may not overlap a sibling's token.
  (`person_profile` scored 0/16 because it overlapped `lookup_person` —
  DEC-39/ADR 0004.)
- Any rename is decided by measurement, one at a time.
- Any description rewrite that changes the opening or adds/removes a MUST
  carries the same re-measure obligation: an ISO probe (one variable, n≥5)
  before merge, numbers recorded beside the code.

### 3.5 The description is English-only (operator, 2026-08-31)

Every tool description MUST be written in plain English. This is the
model's routing surface on every call, and a description that mixes
registers — English prose with a sudden Chinese clause — makes the when/why
signal noisier and the schema inconsistent across tools.

Extreme edge cases MAY carry a quoted non-English token where it is
load-bearing content rather than instruction, and ONLY then:

- `remember` quotes the trigger 記低 because the tool fires on that exact
  word — the token is the feature the router matches on (ADR 0004).
- A tool whose *answer content* is non-English (a place name, a quoted
  phrase from the room) may show it as an example of what the model passes
  in an argument.

The normative prose itself — what, when, MUST/SHOULD/MAY — is always
English. A quoted example is not normative prose. If a description needs
more than one quoted non-English token to do its job, that is a smell that
the rule is being stretched, and the tool goes back to the operator.

### 4. Enforcement

- A structural test scans every `NATIVE_TOOLS` description: first sentence
  contains a when-to-call trigger («Call this when» / «Use when» / «Call this
  for» / «when you»); a MUST, if present, appears before the first SHOULD;
  a description whose opening sentence contains «too long»/«too soft»-class
  permission-suppression tokens fails; a description whose normative prose
  contains CJK characters fails.
- The content-pinning test
  (`test_the_send_reply_schema_carries_the_three_reply_defects`) is REWRITTEN
  to pin the structure, not the prose: the three reply defects must remain in
  the schema, but AFTER the MUST opening and labelled SHOULD.
- Renames and opening rewrites go through the ISO-probe gate (§3), recorded
  as a comment beside the docstring, exactly as ADR 0004 records selection
  numbers.

### 5. Description length follows the three-level ladder

A description is sized by the tool's semantic complexity, never by how much
there is to say about it. Three levels, and every tool declares which one it
is (the enforcement test derives it from the description's shape):

| level | shape | ceiling | examples |
|---|---|---|---|
| **1 — sentence** | what + when-to-call, one or two sentences | ~2 sentences | `weather`, `version`, `now`, `music_status` |
| **2 — sentence + one constraint** | what + when + exactly one MUST/SHOULD that changes behaviour | ~3 sentences | `lookup_person` (scope: this conversation only), `send_to` |
| **3 — sentence + contract** | what + when + a genuine state machine the model must honour | as long as the contract is | `media_fetch` (5 statuses + untrusted), `note_create` (refusal fields) |

Assignment rules:

- A tool is Level 1 unless it has a scope/state/refusal fact the model must
  act on. Most tools are Level 1.
- A Level 3 contract is a list of distinct model-facing behaviours — five
  statuses with five different replies is a contract; five sentences
  explaining the same status is an essay.
- Level 3 is the exception, not the default. If a tool is Level 3, the
  contract block is labelled and the MUST/SHOULD/MAY register (§2) applies
  inside it.

### 5a. First compliance case: `send_reply`

Rewrite the opening per §1 and §2:

> Send a message to the group — the ONLY delivery mechanism for your reply.
> You MUST call this tool exactly once whenever you produce a reply; do not
> return the reply as the assistant's final message instead of calling it
> (that path is an emergency fallback, never a proactive choice).

Then the existing style guidance (burst, addressee, length, scoreboard,
tail, caving, too-soft) as a labelled SHOULD block. This is the variant that
measured 5/5/5 in the ISO probe. Level: 2 (sentence + the MUST; the style
SHOULD block is the voice contract, not routing content).

## Consequences

**Better.** The description becomes a declarative surface instead of an essay;
reasoning and non-reasoning models converge on the same call decision (the
probe's A-column split disappears); future tools have a template instead of a
blank page; the registry's machine-verified fields gain a machine-verified
text field.

**Worse, and accepted.**

- The prose pins in several tests must move (they assert the old essay
  shape). The rewrite is measured, so the test change is not decorative.
- Labelled SHOULD blocks add tokens to every model call. The addition is
  bounded (one label per block, guidance already present) and buys the
  register separation the probe shows is load-bearing.
- `send_reply` is the one tool ADR 0004 deliberately left alone for renames —
  this policy does NOT rename it; it rewrites its description. No conflict.

## Rejected

**«The fallback path causes the bypass — remove recovery.»** The recovery
path (DEC-93) is what stops a non-honouring provider from muting Nora, and it
is deliberately narrow (end_turn only, nothing delivered, through `deliver`).
Removing it restores the exact mute the probe shows would get WORSE as the
description improves the call rate but cannot guarantee it. The provider
non-honour problem (pipeline.py:885-894, ollamacloud accepts-and-ignores
`required`) is real but separate; this policy fixes the surface Nora owns.

**«It is a provider problem — wait for tool_choice to be honoured.»** The ISO
probe shows the description alone swings the decision 0/5 → 5/5 on the SAME
provider, SAME tool_choice. Waiting fixes nothing Nora can control today.

**«Keep the essay; teach the model not to over-infer.»** The doctrine already
tries this (system prompt thousands of tokens back — DEC-92 measured an
always-on prose maxim does not move routing; a schema-token change does). The
surface that moves behaviour is the schema, not another doctrine line.