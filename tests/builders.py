"""Test-side builders for generated fixtures.

`nora.config`'s tree is pydantic (frozen `BaseModel`s), not dataclasses. Every
direct use of `model_copy` or a `nora.config` constructor in the suite is a site
that would break on a future config change; concentrating them here means that
change edits this file, not ten others. Nothing outside this module may
construct or copy a config object.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from nora.config import (
    Config,
    LimitsConfig,
    LoggingConfig,
    TracingConfig,
)
from nora.config import load as _load_config
from nora.domain.gatherings import Gathering, GatheringStatus

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_gathering(
    *, conversation_id: str, title: str, created_by: str, platform: str = "signal",
    unit_id: str = "", status: GatheringStatus = GatheringStatus.PROPOSED,
    when: datetime | None = None, created_at: datetime = datetime(2026, 8, 19, tzinfo=UTC),
) -> Gathering:
    return Gathering(
        id=uuid4(), conversation_id=conversation_id, title=title, created_by=created_by,
        platform=platform, unit_id=unit_id, status=status, when=when, created_at=created_at,
        venue_text=None, venue_place_id=None, venue_name=None, venue_address=None,
        venue_rating=None, venue_price_level="", venue_maps_url=None,
        venue_resolved_at=None, reminder_id=None,
    )


def _with(obj: Any, path: str, value: Any) -> Any:
    head, _, rest = path.partition(".")
    if rest:
        value = _with(getattr(obj, head), rest, value)
    return obj.model_copy(update={head: value})


def override_config(base: Any, changes: dict[str, Any]) -> Any:
    """Return a copy of `base` with dotted-path fields replaced.

        override_config(cfg, {"store.data_dir": p, "engagement.trigger.min_interval_seconds": 0})

    Format-agnostic by contract: a future config change only touches `_with`,
    never a caller.
    """
    for path, value in changes.items():
        base = _with(base, path, value)
    return base


def make_limits(**kw: Any) -> LimitsConfig:
    return LimitsConfig(**{"turns": 4, "total_tokens": 64000, "output_tokens": 12000, **kw})



def make_logging_config(**kw: Any) -> LoggingConfig:
    # No `path`: the log goes to stdout, so there is nothing to resolve against
    # a base directory. The context argument stays because `model_validate` is how the
    # rest of these builders construct config models.
    data = {"level": "INFO", "format": "json", **kw}
    return LoggingConfig.model_validate(data, context={"base": REPO_ROOT})


def make_tracing_config(**kw: Any) -> TracingConfig:
    return TracingConfig(**{
        "enabled": False, "exporter": "file", "endpoint": "", "headers_env": "", **kw,
    })


def copy_example_config(tmp_path: Path) -> Path:
    """Copy the committed template into `tmp_path` and return the copy's path.

    R-B (`docs/maintainers/conventions-rationale.md` «Test structure»): this is the ONE
    place in the suite that knows the template's name. A test reaches it
    through the `example_config_path` / `example_config` fixtures in
    `tests/conftest.py` — never by spelling `config.example.toml` itself — and
    `tests/architecture/test_config_comes_from_the_template.py` is the gate.
    The only tests that read the REAL `config.toml` are the ones whose subject
    is the deployment (the F11 drift gate, the R-24 budgets), pinned there.
    """
    cfg_path = tmp_path / "config.toml"
    shutil.copy(REPO_ROOT / "config.example.toml", cfg_path)
    shutil.copytree(REPO_ROOT / "nora" / "doctrine", tmp_path / "nora" / "doctrine",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    return cfg_path


def load_example_config(tmp_path: Path) -> Config:
    """`copy_example_config`, loaded. For helpers that cannot take a fixture
    (the integration harness, a module-level driver); a test function takes
    the `example_config` fixture instead."""
    return _load_config(copy_example_config(tmp_path))
