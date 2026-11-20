"""Serialize Nora task errors through the native SDK's surviving environment state."""

from __future__ import annotations

from typing import Any


def task_error(exc: Exception, source_url: str) -> dict[str, Any]:
    from evals import backbone as fields

    empty = {
        fields.NORA_ERROR: fields.scrub(f"{type(exc).__name__}: {exc}", source_url),
        fields.NORA_EFFECTS: {"status": "unavailable", "cause": "subject_execution_failed",
                             "drives": None},
        fields.NORA_TOKEN: "", fields.NORA_SAID: 0, fields.NORA_DELIVERY: "",
        fields.NORA_ADMITTED: None, fields.NORA_BLOCKS_EMPTY: None, fields.NORA_CALLS: None,
        fields.NORA_INPUT_TOKENS: None, fields.NORA_OUTPUT_TOKENS: None,
        fields.NORA_CACHED_TOKENS: None, fields.NORA_STOPS: None, fields.NORA_WRITTEN: None,
        fields.NORA_CHANGED_COUNT: None, fields.NORA_STAGED_COUNT: None,
        fields.NORA_WRITTEN_2: None, fields.NORA_CHANGED_COUNT_2: None,
        fields.NORA_STAGED_COUNT_2: None, fields.NORA_CHANGED: (), fields.NORA_JUDGED: "",
        fields.NORA_CHECKS_FAILED: (), fields.NORA_TOOLS: None, fields.NORA_TOOL_RESULTS: None,
        fields.NORA_TOOL_INPUTS: None, fields.NORA_SHOWN_NOW: None, fields.NORA_POSITION: None,
        fields.NORA_WORKING_SET: (), fields.NORA_SHOWN: "", fields.NORA_JUDGED_WHY: "",
        fields.NORA_SHOWN_WHERE: None, fields.NORA_TRANSPORT: None,
    }
    return {"output": "", "environment_state": [
        fields.EnvironmentState(name=name, state=value) for name, value in empty.items()]}
