"""The portal's plan schema accepts every status the gatherings API sends.

`app/src/lib/api/people.ts` parses `/app/v1/events` with a closed `z.enum`; a
`GatheringStatus` the enum does not list fails the parse of the whole list,
and the Plans page shows «Couldn't check» for every plan the viewer has.
"""

from __future__ import annotations

import re
from pathlib import Path

from nora.domain.gatherings import GatheringStatus

PEOPLE_TS = Path(__file__).resolve().parents[2] / "app/src/lib/api/people.ts"


def test_the_portal_plan_enum_is_every_gathering_status():
    source = PEOPLE_TS.read_text(encoding="utf-8")
    plan = source[source.index("export const PlanSchema"):]
    match = re.search(r"status: z\.enum\(\[([^\]]*)\]\)", plan)
    assert match, "PlanSchema's status enum not found"
    portal = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert portal == {s.value for s in GatheringStatus}
