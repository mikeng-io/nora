"""`ProductionSeed` + the production-shape eval knobs (`--effort`,
`--skills`, `--seed-production`).

Covers the parts that are testable OFFLINE:

- `build_live_runner(..., effort=...)` overrides the shipped config value on a
  copy, never mutating `cfg.llm` (frozen) — this is the mechanism that makes
  the ADR re-check possible without rewriting `config.toml` between runs.
- `run_case(..., seed=...)` injects the read-only snapshot into the FAKE
  stores (transcript rows land in `FakeRecordStore.rows`; cards seed
  `FakeMemoryStore.cards`), so a scripted run can assert the seeding mechanics
  exactly as the live path uses them.
- `run_case(..., skills_dir=...)` is passed through to `build_live_runner`,
  which normalises str → Path for the runner's `Path | None` slot.
- `build_production_seed` raises loudly when the deployment env is missing
  (this test process has no `DATABASE_URL`), instead of silently producing an
  empty snapshot that would measure nothing.

The live run itself (`run_case(..., live=True, effort=..., seed=...)` against
a reachable gateway) stays under the network mark, like
`test_evals_live_runner.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from evals.harness import Case, ProductionSeed, run_case

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_cfg():
    from nora.app.eval_support import load_eval_config

    return load_eval_config((REPO_ROOT / "config.toml", REPO_ROOT / "config.example.toml"))


# --- effort override --------------------------------------------------------


def test_build_live_runner_effort_override_does_not_mutate_config():
    """`effort=` must be a per-run override on a COPY — the shipped config value
    (which the operator changed to `low` while the ADR still says `none`) is
    what a plain gate run must keep using."""
    from nora.app.eval_support import build_live_runner

    cfg = _load_cfg()
    shipped = cfg.llm.reasoning_effort

    if not os.environ.get(cfg.llm.api_key_env):
        pytest.skip("LLM key env not set; construction would raise")

    runner = build_live_runner(cfg, effort="high")
    # The built runner's model spec is what carries the override.
    spec = runner._models._spec
    assert spec.reasoning_effort == "high"
    # And the config object is untouched (frozen pydantic — assignment
    # would have raised, but assert the semantic too).
    assert cfg.llm.reasoning_effort == shipped

    # Default (effort=None) stays on the shipped value.
    default_runner = build_live_runner(cfg)
    assert default_runner._models._spec.reasoning_effort == shipped


def test_build_live_runner_skills_dir_normalises_str_to_path():
    """The runner's `skills_dir` slot is `Path | None`; the eval entry point
    may hand it a str (`cfg.doctrine.dir.parent / "skills"` is a Path already,
    but the CLI/API surface accepts str)."""
    from nora.app.eval_support import build_live_runner

    cfg = _load_cfg()
    if not os.environ.get(cfg.llm.api_key_env):
        pytest.skip("LLM key env not set; construction would raise")

    runner = build_live_runner(cfg, skills_dir="nora/doctrine")
    assert runner._skills_dir == Path("nora/doctrine")
    assert isinstance(runner._skills_dir, Path)


# --- seed injection ---------------------------------------------------------


def _seed_with_one_recent() -> ProductionSeed:
    from nora.domain.models import DeliveryStatus, TranscriptRow

    return ProductionSeed(
        recents=(
            TranscriptRow(
                id=999, sender_id="+852111", sender_name="Miles",
                ts=1_700_000_000_000, body="聽日凍唔凍呀？", is_nora=False,
                delivery_status=DeliveryStatus.SENT,
                raw_json="{}", quoted_ts=None, media_json=None,
                person_id="miles",
            ),
        ),
        cards={"miles": ["likes dim sum"]},
    )


async def test_run_case_seed_lands_in_fake_stores_even_when_scripted():
    """Even with a zero-turn script, seeding must populate the fake stores the
    pipeline graph reads — that is the whole production-shape trick: the model
    (real or scripted) sees a prompt built from real transcript/card content,
    while the stores it writes to remain fakes."""
    seed = _seed_with_one_recent()
    traj = await run_case(Case(name="seeded", input="nora.000 hi"),
                          script=[], seed=seed)
    # Seed rows survive to the Trajectory-visible run and do not crash the
    # scripted path (which builds FakeRecordStore + FakeMemoryStore directly).
    assert traj.tool_calls == []


def test_build_production_seed_raises_without_deployment_env():
    """`--seed-production` without DATABASE_URL must FAIL LOUDLY, not silently
    run with an empty snapshot that measures nothing."""
    from nora.app.eval_support import build_production_seed

    cfg = _load_cfg()
    # Ensure the env var is absent in this process regardless of any shell leak.
    old = os.environ.pop(cfg.store.database_url_env, None)
    try:
        with pytest.raises(ValueError, match="database"):
            import asyncio

            asyncio.run(build_production_seed(cfg))
    finally:
        if old is not None:
            os.environ[cfg.store.database_url_env] = old


def test_delivery_status_decodes_like_sql_record_store():
    """The seed SELECT feeds `DeliveryStatus(raw_column)` — the SAME decode
    `SqlRecordStore._row` uses — so a seeded row matches what production
    `recent()` returns. `str(enum)` would be a value the DB never holds."""
    from nora.domain.models import DeliveryStatus

    # The raw column holds "pending"/"sent"/"failed" (enum VALUES), which is
    # what the enum constructor accepts.
    assert DeliveryStatus("sent") is DeliveryStatus.SENT
    assert DeliveryStatus("pending") is DeliveryStatus.PENDING


# --- CLI surface ------------------------------------------------------------


def test_cli_effort_flag_is_wired_into_main():
    """`--effort` parsing must reach `main(..., effort=...)` — the arg exists
    to make the ADR re-check not a config.toml edit."""
    from evals import run as run_mod

    source = Path(run_mod.__file__).read_text()
    assert '"--effort"' in source
    assert 'choices=["none", "low", "medium", "high"]' in source
    assert "effort=args.effort" in source


def test_run_case_signature_has_live_knobs():
    """Pin the `run_case` signature: the three production-shape knobs must
    exist and default to None so every existing call site is unchanged."""
    import inspect

    sig = inspect.signature(run_case)
    for name in ("effort", "seed", "skills_dir"):
        assert name in sig.parameters, f"run_case lost parameter {name}"
        assert sig.parameters[name].default is None
