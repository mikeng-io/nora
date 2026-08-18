"""GET /app/v1/skills — the on-demand skill layer's own metadata (design/12,
DEC-48/50), read-only.

`nora.skills.loader.SkillStore` is NOT the live agent's own skill loader —
the agent path (`nora.agent.factory.build_agent`'s `skills_dir` argument)
wires Strands' own vended `AgentSkills` plugin directly at the same
directory (DEC-50's "the day the operator wires strands...AgentSkills at
these same files, nothing here is rewritten" — that day arrived). This
module builds its OWN `SkillStore` over the identical directory purely to
read the same on-disk metadata (name, description) for the web portal —
never the instructions body, which stays out of the prompt except on
activation (progressive disclosure, design/12 §1) and has no business
reaching a browser either.

`strict=False`: a malformed skill file must not take the whole endpoint
down — skip it, same R-27 posture every other web/* module holds.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from nora.skills.loader import SkillStore


def _scan(skills_dir: Path) -> list[dict]:
    if not skills_dir.is_dir():
        return []
    store = SkillStore(skills_dir, strict=False)
    return [
        {"name": skill.name, "description": skill.description}
        for skill in store.available()
    ]


async def build_skills_payload(*, skills_dir: Path) -> list[dict]:
    """Every loaded skill's public metadata — name, description, and
    whether it's actually reachable (the directory exists and has at least
    parsed without a fatal error). `[]` when the directory is absent —
    that's the honest "no skills configured" state, not an error.

    `SkillStore`'s scan is synchronous filesystem I/O (`nora.infra.blobs.
    filesystem`'s own docstring: "every filesystem call runs in a worker
    thread") — offloaded via `asyncio.to_thread` so a slow disk doesn't
    stall the event loop other requests share."""
    return await asyncio.to_thread(_scan, skills_dir)
