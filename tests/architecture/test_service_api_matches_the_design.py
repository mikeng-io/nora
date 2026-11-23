"""The service API's closed vocabularies are the design's tables, member for member."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from nora.domain.service_api import Contract, OperationOutcome, ReadOutcome, status_for

DESIGN = Path(__file__).resolve().parents[2] / "design" / "service-api"
_ROW = re.compile(r"^\| `([a-z_]+)` \|(.*)\|\s*$")


def _table_after(text: str, heading: str) -> list[list[str]]:
    """The rows of the first table after `heading`, as (name, *cells)."""
    lines = text[text.index(heading):].splitlines()[1:]
    start = next(i for i, line in enumerate(lines) if line.startswith("|"))
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        if match := _ROW.match(line):
            rows.append([match.group(1), *(c.strip() for c in match.group(2).split("|"))])
    return rows


def test_outcomes_match_the_design_tables():
    conventions = (DESIGN / "conventions.md").read_text(encoding="utf-8")
    for heading, family in (("**Reads**", ReadOutcome), ("**Operations**", OperationOutcome)):
        design = {row[0]: int(row[-1]) for row in _table_after(conventions, heading)}
        assert design, f"no outcome table after {heading}"
        assert set(design) == set(get_args(family)), heading
        assert {o: status_for(o) for o in design} == design, heading


def test_contracts_match_the_design_table():
    contracts = (DESIGN / "contracts.md").read_text(encoding="utf-8")
    design = {row[0] for row in _table_after(contracts, "## The contracts")}
    assert design, "no contracts table"
    assert design == set(get_args(Contract))
