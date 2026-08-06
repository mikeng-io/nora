"""The facade `evals/` reads config, agent, doctrine, and tag pieces through.

`evals/` may import `nora.app` and `nora.testing` — nothing else from `nora/`
(a boundary test enforces this: `tests/test_boundaries.py`). `evals/run.py`
and `evals/evaluators.py` used to import `nora.agent.factory`,
`nora.agent.guards`, `nora.config`, `nora.doctrine.loader`, `nora.persons`, and
`nora.tag` directly — none of those are `nora.app`, so all of it funnels
through here instead. Every name below is a straight re-export or a thin
pass-through wrapper; nothing here changes behavior, it only relocates which
module gets to import the lower-level pieces. `nora.app` already imports all
of these directly in `composition.py`, so none of this widens what `nora.app`
itself is allowed to reach.
"""

from __future__ import annotations

from pathlib import Path

from nora.agent.factory import create_client, create_model_provider
from nora.agent.guards import LeakGuard
from nora.agent.registry import ToolRegistry
from nora.config import Config, ConfigError
from nora.config import load as load_config
from nora.doctrine.loader import Doctrine, DoctrineStore
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
from nora.tag import is_tagged

REPLY_GUARD_BUDGETS = ToolRegistry().budgets()
# What the doctrine loader's tool-catalogue gate checks backticked prose
# against, so an eval run loads the shipped doctrine under the same rule
# production does.
TOOL_NAMES = ToolRegistry().tool_names()
# The generated capability section, so an eval assembles the same doctrine
# production does rather than one silently missing a fifth of it.
CAPABILITY_BLOCK = ToolRegistry().capability()

__all__ = [
    "CAPABILITY_BLOCK",
    "REPLY_GUARD_BUDGETS",
    "TOOL_NAMES",
    "Config",
    "ConfigError",
    "Doctrine",
    "DoctrineStore",
    "LeakGuard",
    "Metrics",
    "Persons",
    "create_client",
    "create_model_provider",
    "is_tagged",
    "load_config",
    "load_persons",
]


def load_eval_config(candidates: tuple[Path, ...]) -> Config:
    """Try each candidate path in order; raise the last `ConfigError` if none load.

    `config.toml` (tracked in git, per-deployment) is preferred; `config.example.toml`
    (committed) is the fallback for a local `config.toml` predating the schema.
    """
    last_error: ConfigError | None = None
    for candidate in candidates:
        try:
            return load_config(candidate)
        except ConfigError as exc:
            last_error = exc
    if last_error is None:
        raise ValueError("no config candidates provided")
    raise last_error
