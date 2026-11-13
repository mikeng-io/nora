"""Expose the agent's existing raw-call instrumentation to evaluation callers."""

from nora.agent.replay_capture import client_capture  # noqa: F401
from nora.agent.telemetry import (  # noqa: F401
    end_raw_model_span,
    end_raw_model_span_with_error,
    record_eval_model_request,
    start_raw_model_span,
)
