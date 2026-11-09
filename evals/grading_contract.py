"""An independently selected measurement, bound before paid evaluation work."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nora.subject.eval_evidence import EvidenceError
from nora.subject.replay_diagnosis import memory_scenario

_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_FILES = tuple(
    sorted(
        {str(p.relative_to(_ROOT)) for p in (_ROOT / "evals").rglob("*.py")}
        | {
            "scripts/memory_graph_spike.py",
            "nora/agent/replay_inspection.py",
            "nora/agent/replay_capture.py",
            "nora/agent/telemetry.py",
            "nora/obs/tracing.py",
            "nora/subject/eval_evidence.py",
            "nora/subject/raw_model.py",
            "nora/subject/replay_diagnosis.py",
            "nora/domain/blocks.py",
            "nora/domain/composed.py",
            "nora/domain/prompt.py",
            "nora/domain/block_types.py",
            "nora/agent/punctuation.py",
            "uv.lock",
        }
    )
)
_PURPOSES = {
    "eval": {"diagnosis", "regression", "confirmation"},
    "replay": {"diagnosis", "regression", "confirmation"},
    "rescore": {"diagnosis", "calibration", "retrospective"},
    "diagnostic": {"diagnosis"},
}
_SHA = re.compile(r"[0-9a-f]{64}")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()


def case_scenario(case: Any) -> dict[str, Any]:
    """SDK draw/session UUIDs identify executions, not authored scenarios."""
    return case.model_dump(mode="json", exclude={"session_id"})


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(items)
    if len(result) != len(items):
        raise EvidenceError("grading contract contains duplicate fields")
    return result


@dataclass(frozen=True, slots=True)
class GradingContract:
    """Immutable source bytes; decoded objects never mutate the bound policy."""

    text: str
    sha256: str
    _data: dict[str, Any] = field(repr=False, compare=False)

    @property
    def data(self) -> dict[str, Any]:
        return deepcopy(self._data)

    def receipt(self) -> dict[str, Any]:
        data = self.data
        return {
            "sha256": self.sha256,
            "purpose": data["purpose"],
            "entrypoint": data["entrypoint"],
            "judges": data["judges"],
            "reference_validation": "not established by contract binding",
        }

    def record(self) -> None:
        """Retain the exact private reference bytes, not only an opaque hash."""
        from nora.subject.eval_evidence import current

        evidence = current()
        if evidence is None:
            raise EvidenceError("grading contract capture unavailable")
        evidence.record("grading_contract", sha256=self.sha256, text=self.text)

    def instruction(self, role: str) -> str:
        value = self.data["instructions"].get(role)
        if not isinstance(value, str) or not value.strip():
            raise EvidenceError(f"grading contract has no instruction for {role}")
        return value

    def reference(self, key: str) -> dict[str, Any]:
        value = self.data["cases"].get(key)
        if not isinstance(value, dict):
            raise EvidenceError("case has no frozen grading reference")
        return value

    def bind_case(self, corpus: str, case: Any) -> Any:
        ref = self.reference(f"{corpus}:{case.name}")
        if ref["input_sha256"] != digest(case_scenario(case)):
            raise EvidenceError("scenario differs from the frozen grading contract")
        if ref["expected_output"].strip():
            if not self.data["judges"]:
                raise EvidenceError("case acceptance requires an active judge")
            from evals.rebuilt.entry import ENTRY_POINTS

            md = case.metadata or {}
            port = ENTRY_POINTS.get(md.get("entry", "tag"))
            if not ((port is not None and port.judged_on == "position") or
                    {"social_suitability", "contextual_identity", "lesson_applied"}.intersection(
                        md.get("criteria") or ())):
                raise EvidenceError("selected grader does not consume case acceptance")
        # expected_output is the existing grader-only channel. Nothing is
        # added to the subject's stimulus or context metadata.
        return case.model_copy(update={"expected_output": ref["expected_output"]})

    def verify_cases(self, cases: dict[str, Any]) -> None:
        if set(cases) != set(self.data["cases"]):
            raise EvidenceError("grading references do not cover the exact selected cases")
        for key, case in cases.items():
            self.bind_case(key.split(":", 1)[0], case)

    def memory_reference(self, key: str, inspection: Any) -> dict[str, Any]:
        ref = self.reference(key)
        if ref["input_sha256"] != digest(memory_scenario(inspection)):
            raise EvidenceError("shown scenario differs from the frozen grading reference")
        for unit in ref["units"]:
            if any(str(n) not in inspection.get("new_sources", {}) for n in unit["sources"]):
                raise EvidenceError("required coverage lacks its declared source")
        return ref

    def verify_sample(self, sample_sha256: str, wakes: list[dict]) -> None:
        if self.data["sample_sha256"] != sample_sha256:
            raise EvidenceError("cohort differs from the frozen grading contract")
        expected = {str(w["id"]) for w in wakes}
        if set(self.data["cases"]) != expected:
            raise EvidenceError("grading references do not cover the exact cohort")


def load_contract(
    path: Path | None,
    expected: str | None,
    *,
    entrypoint: str,
    judges: tuple[str, ...],
    selection: dict[str, Any],
) -> GradingContract:
    """No implicit approval, current-candidate regeneration or model dispatch."""
    if path is None or not expected or not _SHA.fullmatch(expected):
        raise EvidenceError("paid evaluation requires --grading-contract and its SHA256")
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise EvidenceError("grading contract fingerprint mismatch")
        text = raw.decode()
        data = json.loads(text, object_pairs_hook=_pairs)
        keys = {
            "version",
            "entrypoint",
            "purpose",
            "judges",
            "implementation",
            "reference_doctrine",
            "instructions",
            "cases",
            "sample_sha256",
            "selection",
        }
        if (
            not isinstance(data, dict)
            or set(data) != keys
            or type(data["version"]) is not int
            or data["version"] != 1
            or data["entrypoint"] != entrypoint
            or data["purpose"] not in _PURPOSES[entrypoint]
            or data["judges"] != list(judges)
            or data["selection"] != selection
        ):
            raise EvidenceError("grading contract selection or purpose mismatch")
        implementation = data["implementation"]
        if not isinstance(implementation, dict) or set(implementation) != set(IMPLEMENTATION_FILES):
            raise EvidenceError("grading contract implementation identity is incomplete")
        for name in IMPLEMENTATION_FILES:
            if implementation[name] != hashlib.sha256((_ROOT / name).read_bytes()).hexdigest():
                raise EvidenceError("grader implementation differs from the fixed measurement")
        if not isinstance(data["reference_doctrine"], str):
            raise EvidenceError("grading reference doctrine must be text")
        if entrypoint == "eval" and judges and not data["reference_doctrine"].strip():
            raise EvidenceError("judged eval requires independent reference doctrine")
        if (
            not isinstance(data["instructions"], dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                for k, v in data["instructions"].items()
            )
            or not isinstance(data["cases"], dict)
        ):
            raise EvidenceError("grading contract references are malformed")
        roles = (
            {"memory_support", "memory_coverage"} if entrypoint in {"replay", "rescore"} else set()
        )
        if set(data["instructions"]) != roles:
            raise EvidenceError("grading contract contains unused or missing instructions")
        if entrypoint != "eval" and data["reference_doctrine"]:
            raise EvidenceError("this entrypoint does not consume reference doctrine")
        if entrypoint == "diagnostic" and data["cases"]:
            raise EvidenceError("diagnostic mode does not consume case acceptance")
        for key, ref in data["cases"].items():
            if (
                not isinstance(key, str)
                or not isinstance(ref, dict)
                or set(ref) != {"input_sha256", "expected_output", "units"}
                or not isinstance(ref["input_sha256"], str)
                or not _SHA.fullmatch(ref["input_sha256"])
                or not isinstance(ref["expected_output"], str)
                or not isinstance(ref["units"], list)
            ):
                raise EvidenceError("grading case reference is malformed")
            ids = []
            if entrypoint == "eval" and ref["units"]:
                raise EvidenceError("standard eval uses expected_output, not memory units")
            if entrypoint != "eval" and ref["expected_output"]:
                raise EvidenceError("memory evaluation uses units, not expected_output")
            for unit in ref["units"]:
                if (
                    not isinstance(unit, dict)
                    or set(unit) != {"id", "claim", "sources"}
                    or not isinstance(unit["id"], str)
                    or not unit["id"].strip()
                    or not isinstance(unit["claim"], str)
                    or not unit["claim"].strip()
                    or not isinstance(unit["sources"], list)
                    or not unit["sources"]
                    or any(type(n) is not int for n in unit["sources"])
                ):
                    raise EvidenceError("required coverage unit is malformed")
                ids.append(unit["id"])
            if len(ids) != len(set(ids)):
                raise EvidenceError("required coverage unit is duplicated")
        if entrypoint in {"replay", "rescore"}:
            for role in ("memory_support", "memory_coverage"):
                if not data["instructions"].get(role):
                    raise EvidenceError("memory grading instructions are incomplete")
        if entrypoint == "replay" and (
            not isinstance(data["sample_sha256"], str) or not _SHA.fullmatch(data["sample_sha256"])
        ):
            raise EvidenceError("replay requires a fixed cohort fingerprint")
    except EvidenceError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise EvidenceError("cannot read a valid fixed grading contract") from exc
    return GradingContract(text, expected, data)


def contract_for(
    args: Any, *, entrypoint: str, judges: tuple[str, ...], selection: dict[str, Any]
) -> GradingContract:
    return load_contract(
        getattr(args, "grading_contract", None),
        getattr(args, "grading_contract_sha256", None),
        entrypoint=entrypoint,
        judges=judges,
        selection=selection,
    )
