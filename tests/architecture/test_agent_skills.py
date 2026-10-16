"""The skills of the agents that BUILD Nora (`.agents/skills/`, ADR 0017) are
well-formed, readable by every tool from one copy, and listed in AGENTS.md.

Not Nora's own runtime skills: those live in `nora/skills/` and are held by
`tests/test_skill_content.py` (ADR 0016).

What is measured, and why each:

- **Shape** (the Agent Skills spec, agentskills.io): a `SKILL.md` whose YAML
  frontmatter has a `name` equal to its directory and a `description`. A tool
  that cannot parse the frontmatter never offers the skill, and nothing reports
  it: the skill is silently absent, which is worse than an error.
- **One copy.** Each `.claude/skills/<name>` is a SYMLINK to
  `../../.agents/skills/<name>`, so Claude Code and every `.agents`-reading tool
  see the same files; a real directory there would be a second copy, and two
  copies drift (ADR 0010's argument). Per skill, not the whole directory: the
  deploy host's checkout already held an untracked personal skill under
  `.claude/skills/`, and a tracked symlink in its place would make every later
  `git pull` there refuse.
- **Listed both ways.** Every skill appears in AGENTS.md's table, and every
  skill the table names exists. ADR 0017 is about rules that are written down
  and not reached at the moment they apply; a skill that the entry point does
  not name is the same failure one level down.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents" / "skills"
CLAUDE_SKILLS = ROOT / ".claude" / "skills"
AGENTS_MD = ROOT / "AGENTS.md"

_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
# A row of AGENTS.md's skills table: `| <when> | `nora-x` |`.
_TABLE_ROW = re.compile(r"^\|[^|]+\|\s*`([a-z0-9-]+)`\s*\|\s*$", re.MULTILINE)

#: The spec allows 1,024 characters; some readers truncate far sooner, and the
#: description is the only text that decides whether the skill loads.
_MAX_DESCRIPTION = 400
#: A skill is a procedure, not a manual (the spec recommends under 500 lines).
_MAX_BODY_LINES = 200


def _skill_dirs() -> list[Path]:
    """The TRACKED skills. The deploy host's checkout holds an operator's
    untracked personal skill under `.agents/skills/`; listing the disk would
    judge it by this repo's rules and fail there."""
    out = subprocess.run(
        ["git", "ls-files", ".agents/skills"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    names = {Path(p).parts[2] for p in out if len(Path(p).parts) > 3}
    return sorted(SKILLS / n for n in names)


def _frontmatter(skill_md: Path) -> dict[str, object]:
    match = _FRONTMATTER.match(skill_md.read_text(encoding="utf-8"))
    assert match, f"{skill_md}: no YAML frontmatter delimited by '---' lines"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{skill_md}: frontmatter is not a mapping"
    return data


def _table_names() -> set[str]:
    text = AGENTS_MD.read_text(encoding="utf-8")
    section = text.split("## Skills: the procedures", 1)
    assert len(section) == 2, "AGENTS.md has no '## Skills: the procedures' section"
    body = section[1].split("\n## ", 1)[0]
    return set(_TABLE_ROW.findall(body))


def test_at_least_one_skill_ships() -> None:
    """Positive control: an empty directory would pass every check below."""
    assert _skill_dirs(), f"no skills under {SKILLS}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_every_skill_is_well_formed(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    assert skill_md.is_file(), f"{skill_dir.name}: no SKILL.md"
    meta = _frontmatter(skill_md)
    assert set(meta) == {"name", "description"}, (
        f"{skill_dir.name}: frontmatter keys {sorted(meta)}; the spec's two are "
        "name and description, and an unknown key is read differently by each tool"
    )
    name = meta["name"]
    assert name == skill_dir.name, f"name {name!r} is not its directory {skill_dir.name!r}"
    assert isinstance(name, str) and _NAME.match(name) and len(name) <= 64, name
    description = meta["description"]
    assert isinstance(description, str) and description.strip(), f"{name}: empty description"
    assert len(description) <= _MAX_DESCRIPTION, (
        f"{name}: description is {len(description)} characters, ceiling {_MAX_DESCRIPTION}"
    )
    body = _FRONTMATTER.sub("", skill_md.read_text(encoding="utf-8"), count=1)
    lines = body.count("\n")
    assert lines <= _MAX_BODY_LINES, f"{name}: body is {lines} lines, ceiling {_MAX_BODY_LINES}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_claude_code_reads_each_skill_through_a_symlink(skill_dir: Path) -> None:
    link = CLAUDE_SKILLS / skill_dir.name
    assert link.is_symlink(), (
        f".claude/skills/{skill_dir.name} must be a symlink to "
        f"../../.agents/skills/{skill_dir.name}, not missing and not a second copy"
    )
    target = os.readlink(link)
    assert target == f"../../.agents/skills/{skill_dir.name}", (
        f"{link} points at {target!r}; a relative link is the only one that "
        "resolves in every clone"
    )


def test_no_claude_skill_link_points_at_nothing() -> None:
    """A link left behind after a skill is renamed or deleted: Claude Code
    would offer a skill whose files are gone. Only TRACKED entries count; an
    operator's personal, untracked skill beside these is theirs."""
    tracked = subprocess.run(
        ["git", "ls-files", ".claude/skills"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    dangling = [p for p in tracked if not (ROOT / p).exists()]
    assert dangling == [], f"tracked .claude/skills links that resolve to nothing: {dangling}"


def test_agents_md_lists_every_skill_and_only_real_ones() -> None:
    on_disk = {p.name for p in _skill_dirs()}
    listed = _table_names()
    assert listed, "AGENTS.md's skills table parsed to nothing; the row pattern is stale"
    unlisted, phantom = sorted(on_disk - listed), sorted(listed - on_disk)
    assert unlisted == [], f"skills AGENTS.md does not list: {unlisted}"
    assert phantom == [], f"AGENTS.md lists skills that do not exist: {phantom}"
