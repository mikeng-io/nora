"""Building the sub-agent worker for the composition root.

`[subagent] enabled = false` (the shipped default) builds NOTHING here: `build_subagent_backend`
is not called, no kind is registered with the poller, and this module's Strands-using imports
(`nora.app.subagent_runner`, the backend) are inside the function, so importing it costs
nothing.

On, it builds one `SubagentBackend` the composition registers under kind `subagent`. The model
is the Dreamer's (`[dreamer]`'s model, effort and `max_tokens`, `tool_choice` AFTER_SPEAKING so
the loop ends when the model stops calling tools); the bounds are `[dreamer.budgets.subagent]`,
which the config REFUSES to load without; the tools are the closed list of
`nora.agent.subagent_tools`; the system prompt is `nora/subagent/doctrine/subagent.md` plus
`nora/doctrine/core/constraints.md`, read once at startup (a missing or malformed file is a
startup failure with its name, not the first job's).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from nora.agent import factory, tool_choice
from nora.agent.registry import ToolRegistry
from nora.agent.subagent_tools import SubagentToolSource
from nora.domain.subagent import SUBAGENT_KIND

SUBAGENT_DOCTRINE = Path("subagent") / "doctrine" / "subagent.md"
CONSTRAINTS = Path("core") / "constraints.md"


def subagent_budget(b: Any) -> Any:
    """`[dreamer.budgets.subagent]` (a `DreamBudgetConfig`) as the worker's own bound: rounds,
    tokens, output tokens and the wall clock, and nothing else. The config refuses a block that
    sets a call ceiling, so no field is dropped here in silence."""
    from nora.app.cognition_session import AdaptorBudget

    return AdaptorBudget(
        turns=b.turns, total_tokens=b.total_tokens, output_tokens=b.output_tokens,
        timeout_seconds=b.timeout_seconds,
    )


def build_subagent_backend(
    *, cfg: Any, client: Any, tier: Any, registry: ToolRegistry, store: Any, persons: Any,
    honcho: Any, metrics: Any, rosters: Mapping[str, Any], self_ids: frozenset[str],
    clock: Callable[[], datetime], result_caps: Mapping[str, int],
    reasoning_effort: str, jobs: Any = None,
) -> Any:
    """The `SubagentBackend` for `[subagent] enabled = true`."""
    from nora.app.subagent_backend import SubagentBackend
    from nora.app.subagent_runner import SubagentRunner, load_system_prompt

    nora_dir: Path = cfg.doctrine.dir.parent
    doctrine_root: Path = cfg.doctrine.dir
    models = factory.ModelSource(
        client=client,
        spec=factory.ModelSpec(
            model=cfg.endpoint("subagent", cfg.dreamer_model())[2],
            max_tokens=cfg.dreamer_max_tokens(),
            temperature=tier.temperature, reasoning_effort=reasoning_effort,
            tool_choice=tool_choice.AFTER_SPEAKING,
        ),
    )
    runner = SubagentRunner(
        models=models,
        retry=factory.build_retry_policy(timeout_seconds=tier.timeout_seconds),
        metrics=metrics,
        tools=SubagentToolSource(
            registry=registry, store=store, persons=persons, honcho=honcho,
            metrics=metrics, rosters=rosters, self_ids=self_ids,
        ),
        system_prompt=load_system_prompt(
            nora_dir / SUBAGENT_DOCTRINE, doctrine_root / CONSTRAINTS,
        ),
        skills_dir=nora_dir / "skills",
        budget=subagent_budget(cfg.dreamer.budget_for(SUBAGENT_KIND)),
        clock=clock, tz=cfg.analytics.tz_name, result_caps=result_caps,
    )

    def requester_name(person_id: str) -> str:
        ref = persons.by_person.get(person_id)
        return ref.name if ref is not None else person_id

    return SubagentBackend(
        runner=runner, metrics=metrics, clock=clock, requester_name=requester_name,
        jobs=jobs,
    )
