"""Every label value the computer audit counter can carry is one that start-up
created at zero (DEC-261).

`nora_computer_audit_failures_total` is a should-be-zero counter: "never
emitted" cannot be told from "never failed" while its series only exist
after the first failure, and `increase()` over a series whose first sample
is already 1 reads 0. `ComputerMetrics.prime()` creates every `(tool, stage)`
at zero from `COMPUTER_AUDIT_STAGES` and the tool names. This reads the source
for the `stage=` and `tool=` literals the call sites pass, so a new stage added
at a call site and not to the tuple is a red test here, not a rare series
nobody can alert on.
"""

from __future__ import annotations

import ast
from pathlib import Path

from nora.agent.tools import COMPUTER_TOOLS
from nora.app.computer_metrics import SWEEPER_AUDIT_TOOL
from nora.domain.computer import COMPUTER_AUDIT_STAGES

ROOT = Path(__file__).resolve().parents[2] / "nora"
NAME = "nora_computer_audit_failures_total"


def _sites() -> list[tuple[Path, int, dict[str, str]]]:
    """Every `metrics.inc("nora_computer_audit_failures_total", ...)` call, with
    the string-literal keyword values it passes (a variable `tool=tool` is not a
    literal and is checked by the tools' own tests)."""
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "inc" and node.args
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == NAME):
                continue
            literals = {
                kw.arg: kw.value.value for kw in node.keywords
                if kw.arg and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            }
            found.append((path, node.lineno, literals))
    return found


def test_the_sites_are_found_so_the_checks_below_can_fail() -> None:
    """A positive control: a scan that reads no call sites would pass every
    assertion below."""
    sites = _sites()
    assert len(sites) >= 10, [(p.name, n) for p, n, _ in sites]
    assert any(lit.get("stage") == "row" for _, _, lit in sites)


def test_every_stage_a_call_site_passes_is_primed() -> None:
    stages = {lit["stage"] for _, _, lit in _sites() if "stage" in lit}
    assert stages <= set(COMPUTER_AUDIT_STAGES), stages - set(COMPUTER_AUDIT_STAGES)


def test_every_stage_that_is_primed_is_one_a_call_site_can_pass() -> None:
    """The other direction: a primed stage nothing increments is a series that
    is genuinely always zero, which is a lie in a different direction."""
    stages = {lit["stage"] for _, _, lit in _sites() if "stage" in lit}
    assert set(COMPUTER_AUDIT_STAGES) - stages == set(), set(COMPUTER_AUDIT_STAGES) - stages


def test_every_tool_a_call_site_names_is_primed() -> None:
    primed = set(COMPUTER_TOOLS) | {SWEEPER_AUDIT_TOOL}
    tools = {lit["tool"] for _, _, lit in _sites() if "tool" in lit}
    assert tools <= primed, tools - primed
