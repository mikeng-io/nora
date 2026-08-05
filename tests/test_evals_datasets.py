"""Tests for `evals/datasets.py` (Phase 7 Task 2, DEC-6). Everything here runs
against `NullEvalSink` or a hand-built recording fake — never a real Langfuse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.datasets import (
    CORPUS_DATASETS,
    CORPUS_PATHS,
    item_id,
    load_corpus_cases,
    upsert_corpora,
)
from evals.sink import NullEvalSink


class _RecordingSink:
    """Records every call instead of talking to anything — lets these tests
    assert on exactly what would have been sent, without needing a fake HTTP
    server (that's `tests/test_evals_sink.py`'s job for `LangfuseEvalSink`
    itself; this file is about `evals/datasets.py`'s mapping logic)."""

    def __init__(self) -> None:
        self.datasets: list[str] = []
        self.items: list[dict[str, Any]] = []

    async def upsert_dataset(self, *, name: str) -> None:
        self.datasets.append(name)

    async def upsert_item(
        self, *, dataset_name: str, item_id: str, input: Any, expected_output: Any = None
    ) -> None:
        self.items.append({
            "dataset_name": dataset_name, "item_id": item_id,
            "input": input, "expected_output": expected_output,
        })

    async def start_run(self, *, dataset_name: str, run_name: str) -> str:
        raise NotImplementedError

    async def record_score(self, **kwargs: Any) -> None:
        raise NotImplementedError


# --- item_id -----------------------------------------------------------------


def test_item_id_prefixes_the_case_name_with_the_corpus_stem():
    assert item_id("trigger", "dinner_time_cantonese") == "trigger/dinner_time_cantonese"


def test_item_id_keeps_same_named_cases_in_different_corpora_distinct():
    assert item_id("injections", "foo") != item_id("near_miss", "foo")


# --- real corpus files: the mapping this module actually ships ---------------


def test_all_five_real_corpus_files_are_present_and_unmoved():
    """Companion to test_r14_is_covered_by_the_adversarial_corpus — this module
    must not have moved or renamed any corpus file."""
    for path in CORPUS_PATHS.values():
        assert path.is_file(), path


def test_three_adversarial_corpora_share_one_dataset():
    assert CORPUS_DATASETS["injections"] == "nora-adversarial"
    assert CORPUS_DATASETS["near_miss"] == "nora-adversarial"
    assert CORPUS_DATASETS["transcript_injection"] == "nora-adversarial"


def test_trigger_and_no_trigger_each_have_their_own_dataset():
    assert CORPUS_DATASETS["trigger"] == "nora-trigger"
    assert CORPUS_DATASETS["no_trigger"] == "nora-no-trigger"


async def test_upsert_corpora_pushes_every_dataset_named_in_corpus_datasets():
    sink = _RecordingSink()
    await upsert_corpora(sink)
    assert set(sink.datasets) == {"nora-trigger", "nora-no-trigger", "nora-adversarial"}


async def test_upsert_corpora_one_item_per_case_with_no_id_collisions():
    sink = _RecordingSink()
    await upsert_corpora(sink)

    total_cases = sum(len(load_corpus_cases(p)) for p in CORPUS_PATHS.values())
    assert len(sink.items) == total_cases

    ids = [row["item_id"] for row in sink.items]
    assert len(ids) == len(set(ids)), "duplicate item ids in the real corpora"


async def test_trigger_item_carries_input_and_expected_output():
    sink = _RecordingSink()
    await upsert_corpora(sink, {"trigger": CORPUS_PATHS["trigger"]})

    row = next(r for r in sink.items if r["item_id"] == "trigger/dinner_time_cantonese")
    assert row["dataset_name"] == "nora-trigger"
    assert row["input"] == "nora.000 幾點食飯"
    assert row["expected_output"] == "答返實際時間，或者話未有共識"


async def test_adversarial_item_uses_expect_as_expected_output():
    sink = _RecordingSink()
    await upsert_corpora(sink, {"injections": CORPUS_PATHS["injections"]})

    row = next(r for r in sink.items if r["item_id"] == "injections/injection_dan")
    assert row["dataset_name"] == "nora-adversarial"
    assert row["expected_output"] == "reply"


# --- idempotency ---------------------------------------------------------


async def test_upsert_corpora_is_idempotent_across_two_runs():
    """Re-running never duplicates: each call is an upsert-by-id, so the
    NullEvalSink-observed behaviour is just "called again with the same
    arguments" — no dedup logic needed on this side, the id IS the dedup key."""
    sink = _RecordingSink()
    await upsert_corpora(sink, {"trigger": CORPUS_PATHS["trigger"]})
    first_count = len(sink.items)
    await upsert_corpora(sink, {"trigger": CORPUS_PATHS["trigger"]})
    assert len(sink.items) == first_count * 2
    assert len({row["item_id"] for row in sink.items}) == first_count


async def test_upsert_corpora_against_the_null_sink_never_raises():
    await upsert_corpora(NullEvalSink())


# --- PROBE (Task 2): a genuine id collision must be caught ------------------


async def test_two_cases_with_the_same_derived_id_raise_value_error(tmp_path: Path):
    """Two cases sharing a `name` within one corpus file derive the same
    `<corpus>/<case-name>` id — this must be caught loudly, not silently
    overwrite one item with the other (a collision here means the corpus YAML
    has a real duplicate-name bug, not two legitimately distinct cases)."""
    rigged = tmp_path / "rigged.yaml"
    rigged.write_text(yaml.safe_dump([
        {"name": "same_name", "input": "first case", "expected_output": "a"},
        {"name": "same_name", "input": "second case", "expected_output": "b"},
    ]))

    with pytest.raises(ValueError, match="duplicate dataset item id"):
        await upsert_corpora(_RecordingSink(), {"trigger": rigged})
