"""GET /app/v1/memory — graph claims about the signed-in viewer."""

from collections.abc import Awaitable, Callable, Collection
from typing import Any, Protocol

from nora.persons import Persons

_PARTS = {"fact": "facts", "trait": "traits", "guess": "guesses"}
_STATES = {"live": "current", "awaiting_weighing": "disputed", "retired": "retired"}


class SubjectClaims(Protocol):
    def __call__(
        self, *, unit: str, viewer: str, viewer_rooms: Collection[tuple[str, str]],
    ) -> Awaitable[tuple[dict[str, Any], ...]]: ...


async def build_memory_payload(
    *, persons: Persons, person_id: str | None,
    workspaces_of: Callable[[str], tuple[str, ...]] | None = None,
    profile: SubjectClaims | None = None,
    profile_scopes_of: Callable[[str], tuple[tuple[str, str], ...]] | None = None,
    rooms_of: Callable[[str], frozenset[tuple[str, str]]] | None = None,
    screen: Callable[[str], str | None],
) -> list[dict[str, Any]]:
    if person_id is None or profile is None or profile_scopes_of is None:
        return []
    viewer = persons.by_person.get(person_id)
    if viewer is None:
        return []
    member_of = None if workspaces_of is None else frozenset(workspaces_of(viewer.person_id))
    viewer_rooms = frozenset() if rooms_of is None else rooms_of(viewer.person_id)
    accounts = []
    for unit, workspace in profile_scopes_of(viewer.person_id):
        if member_of is not None and workspace not in member_of:
            continue
        claims = await profile(unit=unit, viewer=viewer.person_id, viewer_rooms=viewer_rooms)
        lines, disputes = [], []
        for claim in claims:
            line = _line(claim, screen)
            lines.append(line)
            if claim["disputed"]:
                disputes.append({
                    "kind": "dispute", "claim": line["text"], "disputed_by": None,
                    "written_at": line["written_at"],
                })
        if lines:
            accounts.append({"unit": unit, "lines": lines, "disputes": disputes})
    return accounts


def _line(
    claim: dict[str, Any], screen: Callable[[str], str | None],
) -> dict[str, Any]:
    text = claim["content"]
    if screen(text):
        text = "[Claim text withheld]"
    evidence = [
        {"ref": entry["ref"], "kind": entry["kind"]}
        if entry.get("text") and screen(entry["text"]) else entry
        for entry in claim["evidence"]
    ]
    prediction = claim["prediction"]
    return {
        "id": claim["id"], "origin": claim["origin_room"], "text": text,
        "part": _PARTS.get(claim["part"], "other"), "kind": claim["part"],
        "state": _STATES[claim["state"]], "mode": claim["reasoning"], "told_by": None,
        "written_at": int(claim["asserted_at"].timestamp() * 1000),
        "prediction": prediction if prediction is None or screen(prediction) is None else None,
        "prediction_window_end": (
            int(claim["window_end"].timestamp() * 1000)
            if claim["window_end"] is not None else None
        ),
        "verdict": claim["grade"], "evidence": evidence,
    }
