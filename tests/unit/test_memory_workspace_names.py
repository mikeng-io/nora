"""A draw workspace's NAME: in the eval namespace, legal to Honcho, traceable,
unique. The lifecycle against a server is
`tests/integration/test_eval_draws_isolate_honcho.py`."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from nora.subject.memory_workspace import (
    NotADrawWorkspaceError,
    draw_prefix,
    draw_workspace_id,
    new_run_id,
    refuse_unless_draw_workspace,
    shared_eval_workspace,
)


def test_the_prefix_is_the_shared_sibling_plus_a_dash() -> None:
    assert shared_eval_workspace("nora") == "nora-eval"
    assert draw_prefix("nora") == "nora-eval-"


def test_a_draw_name_is_traceable_and_legal_to_honcho() -> None:
    name = draw_workspace_id("nora", run_id="20260924T101500-ab12", case="asks whatsapp?#3")
    assert re.fullmatch(r"nora-eval-20260924T101500-ab12-asks_whatsapp-3-[0-9a-f]{6}", name), name
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", name)


def test_the_same_run_and_case_twice_is_two_names() -> None:
    names = {draw_workspace_id("nora", run_id="r", case="c#1") for _ in range(50)}
    assert len(names) == 50


def test_a_long_case_name_stays_inside_honchos_limit() -> None:
    name = draw_workspace_id("nora", run_id="r", case="x" * 2000)
    assert len(name) <= 512


@pytest.mark.parametrize("workspace", [
    "nora", "nora-eval", "nora-eval-", "nora-evalx", "xnora-eval-1", "nora-48b00d52c0ff",
    "nora-eval-bad/slash",
])
def test_the_guard_refuses_anything_outside_the_draw_namespace(workspace: str) -> None:
    with pytest.raises(NotADrawWorkspaceError):
        refuse_unless_draw_workspace(workspace, "nora")


def test_the_guard_needs_a_deployment_to_derive_from() -> None:
    with pytest.raises(NotADrawWorkspaceError):
        refuse_unless_draw_workspace("-eval-x", "")


def test_a_run_id_names_when_it_started() -> None:
    at = datetime(2026, 9, 24, 10, 15, tzinfo=UTC)
    assert re.fullmatch(r"20260924T101500-[0-9a-f]{4}", new_run_id(at))
