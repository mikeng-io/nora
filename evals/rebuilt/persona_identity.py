"""Deterministic writer acceptance, separate from reply-tool routing."""
from __future__ import annotations

import json
from typing import Any

from nora.subject.persona_writer import run_writer, validate_sources

from .domain import Case, Outcome

REPRESENTATION_PROPERTIES = {
    "categorical_report": "The source author self-reported that they AND unspecified others "
        "were already in their forties on 2026-09-02 (the original source date). "
        "Preserve the inclusive "
        "subject and dated self-report, not a current numeric age, birthday or birth year.",
    "observed_sharing": "The source author shared Instagram reels on 2026-10-01, 2026-10-03 "
        "and 2026-10-05. "
        "Preserve the bounded observation; creator/account ownership and usual frequency "
        "remain unknown. Do not infer preference, proficiency or an enduring habit.",
    "observed_participation": "The source author authored messages on BOTH Signal AND Discord "
        "on 2026-10-01/05 (Signal) and 2026-10-03 (Discord). Preserve the full "
        "conjunction and bounded dates; "
        "account ownership and usual frequency remain unknown.",
}



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
    elif vector in {"profile_cached", "profile_late", "profile_missing"}:
        if vector == "profile_missing":
            if heads:
                failures.append("unread attachment established unsupported identity")
        else:
            h = exact("food_preferences", ["vegetarian ramen"])
            if len(heads) != 1:
                failures.append("profile must establish only its qualified preference")
            if h and (h["mode"] != "deduced" or h["told_by"] is not None
                      or h["as_of"] != receipt["timestamps"][-1]
                      or set(h["citations"]) != {str(i) for i in receipt["rows"]}):
                failures.append("profile preference lost deduced source provenance")
    elif vector in {"profile_endorsed_late", "profile_media_only_late",
                    "profile_media_only_missing"}:
        if vector.endswith("missing"):
            if heads:
                failures.append("incomplete printed premises established unsupported identity")
        else:
            h = exact("food_preferences", ["vegetarian ramen"])
            if len(heads) != 1:
                failures.append("profile must establish only its qualified preference")
            if h:
                provenance_ok = h["mode"] == "deduced" and h["told_by"] is None
                if vector == "profile_endorsed_late":
                    provenance_ok |= (h["mode"] == "told"
                                      and h["told_by"] == receipt["person"])
                if (not provenance_ok or h["as_of"] != receipt["timestamps"][-1]
                        or set(h["citations"]) != {str(i) for i in receipt["rows"]}):
                    failures.append("v2 profile preference lost source provenance")
    elif vector == "categorical_report":
        if not heads:
            failures.append("missing categorical report")
        for h in heads:
            value = h["fact_value"]
            if (h["fact_key"] != "attribute" or not isinstance(value, dict)
                    or set(value) != {"name", "value"} or value["name"] != "statement"
                    or not isinstance(value["value"], str) or not value["value"].strip()):
                failures.append("categorical report requires a qualified string statement")
            if (h["mode"] != "told" or h["told_by"] != receipt["person"]
                    or h["as_of"] != receipt["timestamps"][0]
                    or set(h["citations"]) != {str(receipt["rows"][0])}):
                failures.append("categorical report lost author, date or source")
    elif vector in {"observed_sharing", "observed_participation"}:
        if heads:
            failures.append("observed behavior must not become told identity or ownership")
        traits = [h for h in receipt["heads"]
                  if h["part"] == "character" and h["state"] == "current"]
        if not traits:
            failures.append("missing observed behavior")
        roots = {str(r): ts for r, ts in zip(receipt["rows"], receipt["timestamps"], strict=True)}
        if {c for h in traits for c in h["citations"]} != set(roots):
            failures.append("observed behavior lost source roots")
        for h in traits:
            cited = set(h["citations"])
            if (h["kind"] != "trait" or h["mode"] not in {"deduced", "induced"}
                    or h["told_by"] is not None or h["fact_key"] is not None
                    or h["fact_value"] is not None or not h["text"].strip()
                    or not cited or not cited <= roots.keys()
                    or h["as_of"] != max((roots[c] for c in cited if c in roots), default=None)):
                failures.append("observed behavior lost its mode, transport roots or date")
            if h["valid_from"] is not None or h["valid_until"] is not None:
                failures.append("invented effective date")
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
        validate_sources(private["sources"], now_ms, vector=case.name)
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
            judged="REVIEW" if case.name in REPRESENTATION_PROPERTIES else "",
            evidence=json.dumps({"subject": receipt["person"], "sources": private["sources"],
                                 "rows": receipt["rows"], "timestamps": receipt["timestamps"],
                                 "actual_sources": receipt.get("source_evidence"),
                                 "property": REPRESENTATION_PROPERTIES.get(case.name)},
                                ensure_ascii=False, sort_keys=True),
        )
    except Exception as exc:  # noqa: BLE001 -- unavailable setup/read/write is never a negative PASS
        return Outcome(admitted=None, source="fail_closed", error=f"{type(exc).__name__}: {exc}")
