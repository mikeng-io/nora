"""Corpus -> Langfuse dataset mapping (Phase 7 Task 2, DEC-6).

Each of the five eval YAML files maps onto one of three Langfuse datasets:
`evals/cases/trigger.yaml` -> `nora-trigger`, `evals/cases/no_trigger.yaml` ->
`nora-no-trigger`, and the three `evals/adversarial/*.yaml` files all share ONE
dataset, `nora-adversarial` (spec E.4 treats them as one corpus; R-14's coverage
test — `tests/test_acceptance_rules.py::test_r14_is_covered_by_the_adversarial_
corpus` — pins those three files present, unrelated to and unaffected by which
Langfuse dataset they land in). One dataset item per case: `input` is the case,
`expectedOutput` is whatever that corpus asserts (`expected_output` for the
trigger/no_trigger corpora, `expect` — a bare "reply"/"silence" verdict — for
the adversarial ones).

**Item ids are `<corpus-stem>/<case-name>`, never a bare case name.** Langfuse
item ids are unique per PROJECT across every dataset, not scoped to one dataset
(its own documented constraint) — a collision between, say, an adversarial
corpus case and a trigger-corpus case sharing a name would silently overwrite
one item with the other. Prefixing with the corpus stem (`trigger`,
`no_trigger`, `injections`, `near_miss`, `transcript_injection`) makes that
impossible by construction, since case names are already required to be unique
*within* a single corpus file (`upsert_corpora` enforces this — see its
docstring and the PROBE in `tests/test_evals_datasets.py`).

Idempotent by construction: every push is an `EvalSink.upsert_item` call with
an explicit `id` — Langfuse (and this project's `NullEvalSink`/contract suite)
treat that as create-or-update, never append, so re-running this never
duplicates.

Does NOT move or rename the corpus files — `CORPUS_PATHS` below points at their
existing locations unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from evals.sink import EvalSink

CASES_DIR = Path(__file__).resolve().parent / "cases"
ADVERSARIAL_DIR = Path(__file__).resolve().parent / "adversarial"

# corpus stem -> Langfuse dataset name. Three stems deliberately share one
# dataset — see module docstring.
CORPUS_DATASETS: dict[str, str] = {
    "trigger": "nora-trigger",
    "no_trigger": "nora-no-trigger",
    "injections": "nora-adversarial",
    "near_miss": "nora-adversarial",
    "transcript_injection": "nora-adversarial",
}

CORPUS_PATHS: dict[str, Path] = {
    "trigger": CASES_DIR / "trigger.yaml",
    "no_trigger": CASES_DIR / "no_trigger.yaml",
    "injections": ADVERSARIAL_DIR / "injections.yaml",
    "near_miss": ADVERSARIAL_DIR / "near_miss.yaml",
    "transcript_injection": ADVERSARIAL_DIR / "transcript_injection.yaml",
}


def item_id(corpus: str, case_name: str) -> str:
    """Stable, collision-proof dataset item id — see module docstring."""
    return f"{corpus}/{case_name}"


def load_corpus_cases(path: Path) -> list[dict[str, Any]]:
    """Both yaml shapes the eval suite uses: `evals/cases/*.yaml` is a bare
    list; `evals/adversarial/*.yaml` wraps its list under a top-level `cases:`
    key. `evals.run._load_adversarial_cases` has the same fork for the same
    reason — duplicated rather than imported, since `evals/run.py` is the CLI
    entry point, not a shared library this module should depend on.
    """
    data = yaml.safe_load(path.read_text()) or []
    if isinstance(data, dict):
        return list(data.get("cases", []))
    return list(data)


def _expected_output(case: dict[str, Any]) -> Any:
    """Whatever the corpus asserts — see module docstring."""
    if "expected_output" in case:
        return case["expected_output"]
    return case.get("expect")


async def upsert_corpora(
    sink: EvalSink, corpora: dict[str, Path] | None = None
) -> None:
    """Push every corpus in `corpora` (default: all five, `CORPUS_PATHS`) to its
    Langfuse dataset via `sink`. Idempotent — see module docstring.

    Raises `ValueError` if two cases derive the same item id — i.e. two cases
    share a `name` within the same corpus file, since distinct corpus stems can
    never collide with each other by construction. A real collision here means
    the corpus YAML itself has a duplicate case name, which is a bug in the
    corpus, not something to silently paper over by overwriting one item with
    the other.
    """
    if corpora is None:
        corpora = CORPUS_PATHS
    seen: dict[str, str] = {}
    for corpus, path in corpora.items():
        dataset_name = CORPUS_DATASETS[corpus]
        await sink.upsert_dataset(name=dataset_name)
        for case in load_corpus_cases(path):
            iid = item_id(corpus, case["name"])
            collision = seen.get(iid)
            if collision is not None:
                raise ValueError(
                    f"duplicate dataset item id {iid!r}: both {collision!r} and "
                    f"{corpus!r} produced it"
                )
            seen[iid] = corpus
            await sink.upsert_item(
                dataset_name=dataset_name,
                item_id=iid,
                input=case["input"],
                expected_output=_expected_output(case),
            )
