"""Unit dispatch spies exclude SQL; integration controls exercise the actual effects reader."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from opentelemetry import trace

from evals import backbone
from nora.subject.effects import observed_effects
from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events, validate_events
from nora.subject.sandbox import eval_host_lock


class DispatchCapture:
    def __init__(self, subject):
        self.subject = subject
        self.records = []

    @property
    def latest(self):
        return self.records[-1]

    async def drive(self, port, case, *, now_ms):
        outcome = await port.drive(self.subject, case, now_ms=now_ms)
        self.records.append({"status": "observed", "items": [], "tools": [],
                             "test_scope": "unit dispatch spy"})
        return outcome

    def receipt(self):
        return {"status": "unavailable", "cause": "unit dispatch excludes SQL", "drives": []}


class JudgeBindings:
    def bind(self, config):
        return self

    def bind_evidence(self, effects):
        assert observed_effects(effects)
        return self


@pytest.fixture
def unit_dispatch_capture(tmp_path, monkeypatch, example_config):
    backbone._prefill_capture()

    @asynccontextmanager
    async def dispatch(subject):
        yield DispatchCapture(subject)

    monkeypatch.setattr(backbone, "subject_effects", dispatch)
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id="unit-dispatch", private_root=tmp_path / "capture",
                               metadata={"scope": "unit dispatch"}, lock_fd=lock_fd)
        try:
            with evidence.capture(trace.get_tracer_provider()):
                yield SimpleNamespace(evidence=evidence, config=SimpleNamespace(
                    **{**vars(example_config), "agent": None}))
        except EvidenceError as exc:
            events = read_events(evidence.path)
            assert str(exc) == "incomplete_record"
            assert any(event["kind"] == "case_failed" for event in events)
            assert not any(event["kind"] == "transport_started" for event in events)
            assert validate_events(events)["causes"] == ["capture_interrupted"]
