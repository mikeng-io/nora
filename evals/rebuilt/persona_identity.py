"""Deterministic writer acceptance, separate from reply-tool routing."""
from __future__ import annotations

import json
from typing import Any

from nora.subject.persona_writer import run_writer

from .domain import Case, Outcome


def check_identity(vector: str, receipt: dict[str, Any]) -> tuple[str, ...]:
    """Grade canonical current chain heads; never all rows with state=current."""
    heads = [h for h in receipt["heads"] if h["part"] == "identity" and h["state"] == "current"]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for head in heads:
        by_key.setdefault(head["fact_key"], []).append(head)
    failures = []

    def exact(key: str, value: object) -> dict[str, Any] | None:
        found = by_key.get(key, [])
        if len(found) != 1 or found[0]["fact_value"] != value:
            failures.append(f"{key}: expected exactly {value!r}")
            return None
        return found[0]

    if vector in {"birthday_retained", "birthday_corrected"}:
        corrected = vector == "birthday_corrected"
        h = exact("birthday", {"month": 6, "day": 9 if corrected else 8})
        if h:
            i = -1 if corrected else 0
            if h["as_of"] != receipt["timestamps"][i]:
                failures.append("birthday assertion time changed incorrectly")
            if str(receipt["rows"][i]) not in h["citations"]:
                failures.append("birthday source lost")
            if not corrected:
                original = receipt["held"][0]
                for field in ("support_count", "last_supported_at", "as_of", "told_by", "mode"):
                    if h[field] != original[field]:
                        failures.append(f"birthday {field} changed without support")
                if str(receipt["rows"][-1]) in h["citations"]:
                    failures.append("greeting was counted as birthday evidence")
    elif vector == "residence_unknown":
        if by_key.get("residence"):
            failures.append("incidental location established residence")
    elif vector == "marriage_unknown":
        if by_key.get("relationship"):
            failures.append("past intention established current relationship")
    elif vector == "partner_unspecified":
        exact("relationship", {"relation": "partner", "unresolved_name": "Jo"})
    elif vector == "height_age_stated":
        expected = [{"name": "height", "value": 178, "unit": "cm"},
                    {"name": "age", "value": 34, "unit": "years"}]
        found = by_key.get("attribute", [])
        if len(found) != 2 or any(
            sum(h["fact_value"] == value for h in found) != 1 for value in expected
        ):
            failures.append("expected source-reported height and age as structured attributes")
        for h in found:
            if (h["as_of"] != receipt["timestamps"][0]
                    or str(receipt["rows"][0]) not in h["citations"]
                    or h["mode"] != "told"):
                failures.append("height/age assertion provenance changed")
        if by_key.get("birthday"):
            failures.append("age invented a birthday")
    elif vector == "activity_not_ownership":
        # The source explicitly names a friend; the borrowed activity supports
        # no other durable identity, including ownership or capabilities.
        if heads:
            h = exact("relationship", {"relation": "friend", "unresolved_name": "Jo"})
            if len(heads) != 1:
                failures.append("incidental borrowed activity established durable identity")
            if h and (h["as_of"] != receipt["timestamps"][0]
                      or str(receipt["rows"][0]) not in h["citations"]
                      or h["mode"] != "told"):
                failures.append("friend assertion provenance changed")
    elif vector == "explicit_home_spouse":
        exact("residence", "Harbor District")
        exact("relationship", {"relation": "spouse", "unresolved_name": "Jo"})
    else:
        raise ValueError(f"unknown persona vector {vector!r}")
    # Every accepted identity must carry actual input provenance. This is a
    # structural ceiling; entailment is the writer behavior under measurement.
    for h in heads:
        if not h["fact_key"] or not h["citations"] or h["as_of"] is None:
            failures.append("identity lacks typed source grounding")
        if h["valid_from"] is not None or h["valid_until"] is not None:
            failures.append("invented effective date")
    return tuple(failures)


async def persona_identity(subject: Any, case: Case, *, now_ms: int) -> Outcome:
    try:
        private = case.metadata.get("persona_input")
        if not private or not case.metadata.get("private_persona_file"):
            raise ValueError("persona_identity requires NORA_EVAL_PERSONA_CASES private file input")
        receipt = await run_writer(
            subject, person=case.person, sources=private["sources"],
            held_birthday=case.name.startswith("birthday_"),
            print_input=bool(case.metadata.get("print_input")), now_ms=now_ms,
        )
        if "shown" in receipt:
            return Outcome(admitted=None, shown=receipt["shown"],
                           error="print-input: no model called")
        failed = check_identity(case.name, receipt)
        return Outcome(
            admitted=None, position=json.dumps(receipt, ensure_ascii=False, sort_keys=True),
            checks_failed=failed,
        )
    except Exception as exc:  # noqa: BLE001 -- unavailable setup/read/write is never a negative PASS
        return Outcome(admitted=None, source="fail_closed", error=f"{type(exc).__name__}: {exc}")
