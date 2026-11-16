"""The three result axes every eval report serializes identically.

**Execution** states whether the specified path ran and settled.
**Measurement validity** states whether the declared subject, path and
evidence were valid. **Behaviour** states whether valid evidence
supports the criterion. An axis the evidence cannot establish stays
`UNKNOWN` with its cause — never a manufactured `PASS` — and the
narrower protocol/replay paths carry their claim `scope` rather than a
production-behaviour claim. `UNMEASURED` names a behaviour claim the
path never made.
"""

from __future__ import annotations

from typing import Any

AXES = ("execution", "validity", "behavior")

UNKNOWN = "UNKNOWN"
UNMEASURED = "UNMEASURED"
REVIEW = "REVIEW"


def axis(status: str, *, scope: str | None = None,
         causes: list[str] | tuple[str, ...] = (), **measured: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    if scope:
        out["scope"] = scope
    out.update(measured)
    named = list(dict.fromkeys(c for c in causes if c))
    if named:
        out["causes"] = named
    return out
