"""Identity and inherited scope of computed graph rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from nora.domain.lineage import ROW, Lineage, LineageEntry

Exposure = Literal["external", "internal", "operator"]
SourceKind = Literal["page", "account", "place"]
_EXPOSURE = ("external", "internal", "operator")
DEPLOYED_ROLLUP_PERSONS = frozenset({"rollup_colocation", "rollup_outing"})


def identities_column(table: str) -> str:
    return "persons" if table in DEPLOYED_ROLLUP_PERSONS else "identities"


class StaleBaseError(Exception):
    """A revision named a base that already has a successor."""


def aggregate_graph_id(
    family: str, key: tuple[Any, ...], interval: tuple[datetime, datetime] | None,
) -> str:
    """Return a graph id for a semantic key and its interval, if any."""
    identity = json.dumps((family, key, interval), default=str, ensure_ascii=False)
    return f"aggregate:{hashlib.sha256(identity.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class InputScope:
    excluded_identities: tuple[str, ...]
    exposure: Exposure
    lineage: Lineage


def _excluded(entries: Iterable[LineageEntry]) -> Iterable[str]:
    for entry in entries:
        yield from entry.exclude_persons
        yield from _excluded(entry.reads)


def inherited_scope(
    inputs: Iterable[InputScope], *, build_lineage: Lineage, minimum: Exposure,
) -> InputScope:
    """Retain every input restriction and the most restrictive exposure."""
    exclusions = set(_excluded(build_lineage.section(ROW)))
    lineage = build_lineage
    exposure = minimum
    for source in inputs:
        exclusions.update(source.excluded_identities)
        exclusions.update(_excluded(source.lineage.section(ROW)))
        if _EXPOSURE.index(source.exposure) > _EXPOSURE.index(exposure):
            exposure = source.exposure
        lineage = lineage.union(source.lineage)
    return InputScope(tuple(sorted(exclusions)), exposure, lineage)
