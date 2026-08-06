"""The only module that loads skills — domain instruction bundles, activated on
demand (design/12 §1, authority item 5).

**Why this is not the doctrine loader.** `nora.doctrine.loader` assembles the
files that go into `system_prompt=` on EVERY turn: who she is, how she behaves,
the generated capability block. A skill is the opposite shape. design/12 §1's
placement rule is "if it is needed on every turn it is a prompt … if it is
instructions for a domain that is usually irrelevant it is a skill", and a skill
costs a tool call to activate (design/12 §4). So a skill's BODY must not sit in
the prompt — only its name and its when-to-load line do (progressive
disclosure), and the body arrives when something reaches for it. That is a
different lifetime from doctrine and belongs in a different module. design/17 §4
names exactly five doctrine kinds and "skill" is not one of them, which is the
same statement said in the taxonomy's own words.

**The authority boundary this module exists to hold (design/12 §2, R-29).**
A skill is authority item 5: operator-authored, on disk, and therefore trusted —
it may add procedure, never relax a rule above it. A MESSAGE is item 6 and
untrusted. The load-bearing consequence: *skill sources are operator
configuration only, and a message must never name a skill to load.* If a message
could introduce a skill source, item 6 would reach item 5 and the ordering
collapses (design/14 §2.1 says the same for retrieved web content: "no path from
retrieved text to skill activation").

This is enforced structurally, not by inspection:

- The ONLY input to `_scan` is `self._dir`, a path the composition root fixes
  from operator config. No method takes skill CONTENT or a skill SOURCE as an
  argument.
- `resolve(name)` is a pure lookup against the already-loaded set. It never
  touches the filesystem and never treats its argument as a path, a URL or
  inline text — so the most a message can do is name a skill the operator
  already shipped (which is the intended activation, design/12 §4), and it can
  never name a NEW one into existence. Reverting `resolve` to load from its
  argument the way `strands...Skill.from_file`/`from_url` do is exactly the
  violation R-29's probe turns red.

**Shipped INERT (2026-08-07, DEC-48).** Nothing constructs a `SkillStore` in
`nora.app.composition` yet, and no skill body reaches the model, because
activation needs a model-facing tool with an R-7 budget and that budget table is
`nora.agent.tools.NATIVE_TOOLS` — one number the operator adds to turn this on,
the same DEC-42 shape multi-message shipped in. The file format is the
AgentSkills.io SKILL.md shape (`name` + `description` frontmatter, markdown
body), so the day the operator wires `strands.vended_plugins.skills.AgentSkills`
at these same files, nothing here is rewritten.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml

# The AgentSkills.io name rule (strands...skills.skill._SKILL_NAME_PATTERN),
# reproduced so a file loaded here would load identically under the vended
# plugin later. It is also the `resolve` gate: a path, a URL or an inline
# `---` blob cannot match it, so none can name a skill.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_MAX_NAME = 64
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_REQUIRED = ("name", "description")


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
        disclosure) — the vended AgentSkills shape, ready for the day activation
        is wired. Bodies are NOT included; only name and when-to-load.
        """
        if not self._skills:
            return "<available_skills>\nNo skills are currently available.\n</available_skills>"
        lines = ["<available_skills>"]
        for skill in self.available():
            lines.append("<skill>")
            lines.append(f"<name>{escape(skill.name)}</name>")
            lines.append(f"<description>{escape(skill.description)}</description>")
            lines.append("</skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)


def _skill_md(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md"):
        candidate = skill_dir / name
        if candidate.is_file():
            return candidate
    return None
