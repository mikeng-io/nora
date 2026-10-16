"""The only module that loads skills — domain instruction bundles, activated on
demand (authority item 5).

**Why this is not the doctrine loader.** `nora.doctrine.loader` assembles the
files that go into `system_prompt=` on EVERY turn: who she is, how she behaves.
A skill is the opposite shape: what is needed on every turn is a prompt;
instructions for a domain that is usually irrelevant are a skill, and a skill
costs a tool call to activate. So a skill's BODY must not sit in the prompt —
only its name and its when-to-load line do (progressive disclosure), and the
body arrives when something reaches for it. That is a different lifetime from
doctrine and belongs in a different module; "skill" is not a doctrine kind.

**The authority boundary this module exists to hold (R-29).**
A skill is authority item 5: operator-authored, on disk, and therefore trusted —
it may add procedure, never relax a rule above it. A MESSAGE is item 6 and
untrusted. The load-bearing consequence: *skill sources are operator
configuration only, and a message must never name a skill to load.* If a message
could introduce a skill source, item 6 would reach item 5 and the ordering
collapses: there is no path from retrieved text to skill activation.

This is enforced structurally, not by inspection:

- The ONLY input to `_scan` is `self._dir`, a path the composition root fixes
  from operator config. No method takes skill CONTENT or a skill SOURCE as an
  argument.
- `resolve(name)` is a pure lookup against the already-loaded set. It never
  touches the filesystem and never treats its argument as a path, a URL or
  inline text — so the most a message can do is name a skill the operator
  already shipped (which is the intended activation), and it can
  never name a NEW one into existence. Reverting `resolve` to load from its
  argument the way `strands...Skill.from_file`/`from_url` do is exactly the
  violation R-29's probe turns red.

**This module is not on the live agent's path (DEC-50).**
`nora.agent.factory.create_agent` wires Strands' own vended
`AgentSkills(str(skills_dir), strict=False)` directly at `nora/skills/`; that
plugin never calls `parse_skill` or `SkillStore`. This module is used in two
places: `nora/web/skills.py` builds its OWN `SkillStore(skills_dir,
strict=False)` over the same directory to read the identical on-disk
metadata for the private portal, and the test suite (`tests/test_skill_
content.py`, `tests/test_skill_loader.py`) and `nora-land`'s gate build one
`strict=True` (the default), which is where a malformed or refused file
actually stops something.

**So a refusal `parse_skill` raises is a GATE check, not a runtime one**
`SkillError` here — including
`_FORBIDDEN_AUTHORITY_KEYS` below — is caught wherever `SkillStore` runs
strict, and nothing else: the live agent's own loader (Strands'
`AgentSkills`) has no idea this module's rules exist and would load a
keyed skill's body into a turn regardless, and the portal's non-strict
`SkillStore` in `nora/web/skills.py` drops a keyed skill from its listing
silently (the same R-27 "skip it" posture every malformed-skill case there
gets, deliberately: it is not logged or counted differently from any other
parse failure).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nora.domain.blocks import ContextBlock, render_elements

# The AgentSkills.io name rule (strands...skills.skill._SKILL_NAME_PATTERN),
# reproduced so a file loaded here loads identically under the vended
# plugin. It is also the `resolve` gate: a path, a URL or an inline
# `---` blob cannot match it, so none can name a skill.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_MAX_NAME = 64
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_REQUIRED = ("name", "description")

# The authority vocabulary `nora.doctrine.loader` reads off a DOCTRINE
# section's frontmatter — `authority_above`/`authority_below` (the authority
# graph between doctrine sections) and `change_policy` (who may edit one
# without asking). A skill is a DIFFERENT authority item (below a doctrine
# section, above a message) and nothing reads any of the three off a skill's
# frontmatter: `nora.doctrine.loader` only opens files under `nora/doctrine/`.
# A skill file declaring `authority_above: [situation]` would read as though it
# placed the skill in that graph while nothing wired it in, so `parse_skill`
# refuses the key rather than leaving it in `metadata`.
#
# REJECTED rather than honoured. Honouring them would mean this loader
# participating in the doctrine authority graph — a bigger change. Refusing
# stops a file carrying a claim nothing enforces.
#
# **`change_policy` belongs here too.** Unlike the other two it IS read and
# enforced — `nora.doctrine.loader` and `nora.instructions.dreamer` both
# validate and store it — but neither opens a file under `nora/skills/`; both
# read only their own directories. For a SKILL, `nora/web/skills.py::
# build_skills_payload` is the only reader of parsed metadata, its payload
# carries `name`, `description` and `instructions` only, and the portal read it
# feeds is GET-only with no skill-editing surface for a policy to gate. So a
# skill's `change_policy` is as unenforced as `authority_above`/
# `authority_below`, and is refused for the same reason.
_FORBIDDEN_AUTHORITY_KEYS = ("authority_above", "authority_below", "change_policy")


class SkillError(Exception):
    """A skill problem that stops the process (strict) or skips the file (lenient)."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str          # the when-to-load line — the only body text in the prompt
    instructions: str         # the markdown body — reaches the model only on activation
    path: Path
    sha256: str
    metadata: dict[str, Any]


def parse_skill(path: Path) -> Skill:
    """Parse one SKILL.md into a `Skill`. Frontmatter needs `name` + `description`."""
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillError(f"{path.name}: missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"{path.name}: unparseable frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError(f"{path.name}: frontmatter must be a mapping")
    missing = [k for k in _REQUIRED if not str(meta.get(k, "")).strip()]
    if missing:
        keys = ", ".join(repr(k) for k in missing)
        raise SkillError(f"{path.name}: missing or empty required key(s): {keys}")
    carried = [k for k in _FORBIDDEN_AUTHORITY_KEYS if k in meta]
    if carried:
        keys = ", ".join(repr(k) for k in carried)
        raise SkillError(
            f"{path.name}: carries authority key(s) {keys} — a skill is not "
            "in the doctrine authority graph and nothing here reads them; "
            "remove from front matter (issue #187 finding 3)"
        )
    name = str(meta.pop("name")).strip()
    if len(name) > _MAX_NAME or not _NAME_RE.match(name):
        raise SkillError(
            f"{path.name}: name {name!r} must be 1-{_MAX_NAME} lowercase "
            "alphanumeric characters or hyphens, not starting or ending with one"
        )
    description = str(meta.pop("description")).strip()
    body = match.group(2).strip("\n")
    if not body.strip():
        raise SkillError(f"{path.name}: a skill with no instructions bundles nothing")
    return Skill(
        name=name,
        description=description,
        instructions=body,
        path=Path(path),
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        metadata=dict(meta),
    )


class SkillStore:
    """Operator-configured skills, loaded from one directory and looked up by name.

    `dir` is the sole source of skills and is set by the composition root from
    operator configuration — never from a message (R-29). Each skill lives in a
    subdirectory whose name matches the skill's `name`, holding a `SKILL.md`,
    matching the AgentSkills.io on-disk layout.
    """

    def __init__(self, dir: Path, *, strict: bool = True) -> None:
        self._dir = Path(dir)
        self._strict = strict
        self._skills: dict[str, Skill] = {}
        self._scan()

    def _scan(self) -> None:
        skills: dict[str, Skill] = {}
        for child in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            md = _skill_md(child)
            if md is None:
                continue
            try:
                skill = parse_skill(md)
            except SkillError:
                if self._strict:
                    raise
                continue
            if skill.name != child.name:
                msg = (f"{md}: skill name {skill.name!r} does not match its "
                       f"directory {child.name!r}")
                if self._strict:
                    raise SkillError(msg)
                continue
            if skill.name in skills:
                raise SkillError(f"duplicate skill name {skill.name!r}")
            skills[skill.name] = skill
        self._skills = skills

    def available(self) -> tuple[Skill, ...]:
        """Every loaded skill, name-ordered — the progressive-disclosure metadata."""
        return tuple(self._skills[name] for name in sorted(self._skills))

    def resolve(self, name: str) -> Skill | None:
        """A loaded skill BY NAME, or `None`. The authority guard (R-29).

        Pure lookup against the operator-loaded set. It never reads the
        filesystem and never treats `name` as a path, URL or inline content, so
        a message can at most name a skill the operator already shipped and can
        never introduce a source. The name-shape pre-check makes that explicit:
        a `/`, a `..`, a scheme or an inline `---` block cannot satisfy it, so
        such an argument is refused before the lookup even runs.
        """
        if not isinstance(name, str) or not _NAME_RE.match(name):
            return None
        return self._skills.get(name)

    def render_available(self) -> str:
        """The `<available_skills>` block for the system prompt (progressive
        disclosure) — the vended AgentSkills shape.
        Bodies are NOT included; only name and when-to-load.
        """
        # CLASS 2 throughout — a list of skills the OPERATOR installed, not
        # anything a member typed, which is what the block's type says it is.
        #
        # `name` and `description` are ATTRIBUTES, not nested blocks: nested blocks
        # would each need a description of their own and spread `<name>` over
        # three lines. They are FIELDS of a skill, and the element form's rule is
        # that identifying fields are attributes.
        body = "No skills are currently available."
        if self._skills:
            body = render_elements(
                list(self.available()), tag="skill",
                attrs=lambda sk, _i: (
                    ("name", sk.name), ("when_to_load", sk.description),
                ),
                # The BODY is never included — that is progressive disclosure,
                # and it is why this block can list every skill cheaply.
                body=lambda _sk: "",
            )
        return ContextBlock(
            name="available_skills", body=body,
        ).render()


def _skill_md(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md"):
        candidate = skill_dir / name
        if candidate.is_file():
            return candidate
    return None
