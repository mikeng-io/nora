"""The single reload-outcome vocabulary, shared by recording and metric help."""

from typing import Any, Literal, get_args

ReloadOutcome = Literal["applied", "refused", "restart_required"]
RELOAD_OUTCOMES = get_args(ReloadOutcome)


def record_reload(metrics: Any, file: str, outcome: ReloadOutcome) -> None:
    if outcome not in RELOAD_OUTCOMES:
        raise ValueError("unknown config reload outcome")
    metrics.inc("nora_config_reloads_total", file=file, outcome=outcome)
