"""A run's id carries its own start instant, and the private-blank sweep reads it back."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from nora.subject import sandbox
from nora.subject.run_id import new_run_id


def test_a_run_id_names_when_it_started_and_the_sweep_reads_it_back() -> None:
    at = datetime(2026, 9, 24, 10, 15, tzinfo=UTC)
    run_id = new_run_id(at)
    assert re.fullmatch(r"20260924T101500-[0-9a-f]{4}", run_id)
    assert sandbox._private_blank_created_at(f"nora_eval_blank_run_{run_id}") == at
