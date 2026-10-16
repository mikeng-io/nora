"""GET /app/v1/skills — the on-demand skill layer's metadata (DEC-48/50),
read-only.

`nora.skills.loader.SkillStore` is NOT the live agent's own skill loader —
the agent path (`nora.agent.factory.build_agent`'s `skills_dir` argument)
wires Strands' own vended `AgentSkills` plugin directly at the same
directory (DEC-50). This module builds its OWN `SkillStore` over the identical directory purely to
read the same on-disk metadata for the web portal.

**The instructions body IS included.** The portal is private (auth-gated, same
as every other /app/v1/* endpoint) and shows what a skill actually says, not
just its name and when-to-load line. Progressive disclosure governs the PROMPT
— the body stays out of the model's context until activation — and is
untouched by this read-only metadata endpoint.

`strict=False`: a malformed skill file must not take the whole endpoint
down — skip it, same R-27 posture every other web/* module holds.

**A skill refused for carrying an authority key drops here too, silently**
`parse_skill` refuses `authority_above`,
`authority_below` or `change_policy` in a skill's frontmatter, and this
module's `strict=False` catches that `SkillError` the same as any other
parse failure — the skill is simply absent from the payload, with nothing
logged or counted for THIS reason specifically. That is deliberate rather
than an oversight: every other malformed-skill case here is already silent
in the same way, and singling this one out for a log line or a metric would
be the inconsistency, not the gap. It is the `strict=True` build (the
default — `tests/test_skill_content.py`, `tests/test_skill_loader.py`, and
`nora-land`'s gate) where a carried key actually stops something, before a
skill with one ever ships.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nora.skills.loader import SkillStore


def _scan(skills_dir: Path) -> list[dict[str, Any]]:
    if not skills_dir.is_dir():
        return []
    store = SkillStore(skills_dir, strict=False)
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.instructions,
        }
        for skill in store.available()
    ]


async def build_skills_payload(*, skills_dir: Path) -> list[dict[str, Any]]:
    """Every loaded skill's public metadata — name, description, and the
    instructions body (the portal is private, so it shows the body).
    `[]` when the directory is absent — that's the honest "no skills
    configured" state, not an error.

    `SkillStore`'s scan is synchronous filesystem I/O (`nora.infra.blobs.
    filesystem`'s own docstring: "every filesystem call runs in a worker
    thread") — offloaded via `asyncio.to_thread` so a slow disk doesn't
    stall the event loop other requests share."""
    return await asyncio.to_thread(_scan, skills_dir)
