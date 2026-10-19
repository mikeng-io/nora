---
name: nora-runtime-skill
description: Adds, changes or retires one of Nora's own runtime skills in nora/skills/ under ADR 0016, covering placement, the reach and cost tests from Langfuse, description and body limits, the enforcing tests, and the image rebuild that deploys it. Load when a task touches a SKILL.md under nora/skills or asks why her skill did not load. Not for the agents' skills in .agents/skills, or tools (nora-tool).
---

# Adding or changing one of Nora's runtime skills

This is about the skills NORA loads at runtime (`nora/skills/<name>/SKILL.md`,
read by `nora/skills/loader.py` and served through the `skills` tool), not the
agent skills in `.agents/skills/`. The source of truth is
`docs/adr/0016-skill-policy.md` (status: proposed; read it in full), which
extends `design/12-prompt-tool-skill-boundaries.md` §1. Its §9 table says which
rule is a test and which is review. This skill is the order and the checks.

## 1. If a skill "does not load", read her input first

Before changing a description, the loader or the routing, open the failing turn's
trace (`nora-readback`) and read what the model was given: is the skills block in
the prompt, did she call `skills`, and what else in the prompt answers the same
question. Do not hardwire loading (a skill loaded because its tool was, or because
a message names it): that is deterministic routing, and R-29 forbids the second.
(2026-09-24: an image skill "not loading" was a stale memory card in her input,
not routing.)

## 2. Placement: is it a skill at all?

Decide in this order (0016 §1) and write the answer in the PR:
1. Needed on every turn: doctrine (`nora/doctrine/`).
2. A capability: a tool (`nora-tool`).
3. A rule about calling one tool: that tool's description (`nora-tool`).
4. Judgement other domains also need: doctrine. A skill cannot rely on another
   skill being loaded in the same turn.
5. Only then: judgement for a domain most turns do not touch, too large for one
   tool description.

## 3. Measure before (never carry a number over)

Read these from Langfuse for THIS decision, with the window and n (how to query
it is `nora-readback`; 0016's Sources section names the endpoints it used). The
numbers in 0016's own table are dated and are never an input:

| symbol | what | read as |
|---|---|---|
| loads | `skills` tool observations for this skill | count in the window |
| domain turns | tag turns that called this skill's tools | count, and how many of them loaded it |
| k | mean model calls per tag turn | over a stated sample of traces |
| r | fraction of tag turns that load the skill | loads / tag turns |
| C | input tokens of one load call | from the load's model call |
| B | body tokens (cl100k) | the command in step 5 |

1. **Reach test.** The skill SHOULD load in the `tool_selection` rows labelled
   should-load (`tools: [skills]`), and not in `not_tools: [skills]` rows. The
   starting bar is 80% of the should-load rows; nothing is decided on fewer than
   20 labelled turns. **No such rows exist yet** (0 in
   `evals/corpora/tool_selection.yaml`, 2026-09-24; 0016 §8 lists them as to do):
   add them, or state in the PR that reach is unmeasured. Adding cases and
   running the corpus is `nora-eval`. The Langfuse domain count is a monitor,
   not the verdict.
2. **Cost test.** Doctrine is cheaper when B × k < r × C.
3. **When reach fails,** fix the description first (it is what triggers loading)
   and re-measure. If it still fails, reach outranks cost: the rule that must
   always hold moves to doctrine; the rest stays a skill only if it passes the
   cost test.

## 4. Write it

Copy the frontmatter of an existing skill (`nora/skills/navigation/SKILL.md`):
`name` and `description` only. `authority_above`, `authority_below` and
`change_policy` FAIL THE GATE (`nora/skills/loader.py::parse_skill` raises
`SkillError`, issue #187 finding 3) — nothing reads any of the three for a
skill the way `nora/doctrine/loader.py` reads the first two for a doctrine
section, and nothing reads a skill's `change_policy` anywhere either. A
skill still ranks below doctrine and above message content, may add
procedure, and never relaxes a rule above it (0016 §6) — by convention and
review now, not by a frontmatter key.

1. **Name**: a noun phrase for the domain, lowercase words and single hyphens,
   at most 64 characters, equal to its directory (the loader refuses a mismatch).
2. **Description**, in this order: what the skill covers, when to load it, what
   it is not for. At most 80 cl100k tokens. It speaks to Nora in the SECOND
   person (0016 §3 departs from the third-person advice on purpose). It must
   contain "Not for" or "not for" and be longer than 40 characters.
3. **No named scenario** (a particular trip, meal or occasion) in the description
   or the body. The test only checks the words listed for that skill in
   `_SCENARIOS` in `tests/test_skill_content.py`; a skill with no entry there is
   checked against nothing, so give a new skill its own entry.
4. **Body**: judgement only. It does not describe how a tool works (that is the
   tool's description) and does not restate doctrine (it may point to it). One
   rule per section, and the heading states the rule.
5. **Body ceiling**: under 1,500 cl100k tokens, measured on the body with
   whitespace collapsed to single spaces before encoding (issue #187 finding
   4 — a raw count is not reflow-invariant; see `_body_tokens` in
   `tests/test_skill_content.py`). A skill above that gets its own number and
   reason in `_TOKEN_CEILINGS` there; 5,000 is the hard limit. Argue a raise
   there; never bump it silently.
6. **Writing**: RFC 2119 words only for what is required, plain prose for
   judgement; English except quoted speech and at most one non-English trigger;
   no em or en dash.
7. **Humanize, then audit.** Run the humanizer skill where the session has it.
   A rewrite is then reviewed by someone told to find the rules it LOST (the
   first rewrite under 0016 had lost three and softened three more).

## 5. Check it

```bash
uv run python -c "
import tiktoken; from pathlib import Path; from nora.skills.loader import SkillStore
e=tiktoken.get_encoding('cl100k_base')
for s in SkillStore(Path('nora/skills')).available():
    body = ' '.join(s.instructions.split())  # the ceiling's own measure
    print(s.name, 'description', len(e.encode(s.description)), 'body', len(e.encode(body)))"
uv run pytest -q tests/test_skill_content.py tests/test_skill_loader.py > "${TMPDIR:-/tmp}/skill-tests.log" 2>&1; RC=$?
echo "RC=$RC"; grep -E 'passed|failed|error' "${TMPDIR:-/tmp}/skill-tests.log"
```

PASS is `RC=0` and a passed count. Then `make gate` through `nora-land`.

What is enforced today: the name matches the directory, and no
`authority_above`/`authority_below`/`change_policy` survives load
(`nora/skills/loader.py`) — a GATE refusal, live wherever `SkillStore` runs
strict (this check, and `nora-land`'s gate), not a runtime one: the live
agent loads skills through Strands' own `AgentSkills(strict=False)`
(`nora/agent/factory.py`), which never calls this loader, and the portal's
`SkillStore(strict=False)` (`nora/web/skills.py`) drops a keyed skill from
its listing silently rather than raising. The not-for half, the 80-token
description, no listed scenario, the body ceiling, no dashes
(`tests/test_skill_content.py`); a message can name a skill but never load
one (`tests/test_skill_loader.py`). Review only: the description says what
the skill covers, second person, judgement only, RFC 2119 register.

## 6. Measure after, and write the PR

1. Re-read the same Langfuse numbers, and run the `skills` rows in
   `tool_selection` plus the behaviour cases the skill exists for (`nora-eval`).
2. The PR states the placement answer, B, k, r, C and the reach result, each
   with its window and n, and says the humanizer and the lost-rule audit ran. A
   load adds one model round trip; say so when latency matters.

## 7. Deploy: the image, not the file

`nora/skills` is copied into the image (`COPY nora/` in the `Dockerfile`), and
`docker-compose.yml` bind-mounts only `nora/doctrine` and `nora/dreamer`. A skill
edit is live only after a rebuild and recreate (`nora-deploy`); editing the file
on the host deploys nothing and reports nothing. Read it back:

```bash
git fetch origin master && git show origin/master:nora/skills/<name>/SKILL.md | shasum -a 256
ssh nora-host 'cd ~/workspace/nora && docker compose exec -T nora sha256sum /app/nora/skills/<name>/SKILL.md'
```

Check: the two hashes are equal. Different means the running image is not your
merge; an error means the container or path is wrong, which is not a pass.

## Tool notes

- **Claude Code with the rtk hook:** pytest and grep output can come back
  condensed. Read the log file as written above, or run through `rtk proxy`.
