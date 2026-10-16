"""The boot self-test — the guard against a process that starts up unable to
wake and says nothing about it.

The failure this reproduces: a restarted service with identical code, config,
model and threshold arms gates and never fires, while the container reports
healthy and /health answers 200. PASS / FAIL / ERROR are asserted as three
distinct outcomes because collapsing FAIL or ERROR into "not pass" hides that
state.
"""

from pathlib import Path

import numpy as np
import pytest

from wakegate.audio import OWW_CHUNK
from wakegate.selftest import DEFAULT_CLIP, run_self_test

pytestmark = pytest.mark.skipif(
    not DEFAULT_CLIP.is_file(), reason="the self-test clip is not present"
)


def _always(value: float):
    return lambda _chunk: value


def test_a_scorer_that_wakes_passes_and_reports_its_peak():
    result = run_self_test(score=_always(0.93), threshold=0.5)

    assert result.status == "pass"
    assert result.peak == pytest.approx(0.93)
    assert result.elapsed_ms >= 0.0


def test_a_scorer_that_never_crosses_the_threshold_FAILS_and_is_not_an_error():
    """Audio is scored fine, nothing wakes.

    FAIL and ERROR must stay distinct — they point at opposite things. FAIL
    says the model saw the audio and did not match it; ERROR says the path
    could not be exercised at all."""
    result = run_self_test(score=_always(0.04), threshold=0.5)

    assert result.status == "fail"
    assert result.peak == pytest.approx(0.04)
    assert "0.040" in result.detail and "0.50" in result.detail


def test_a_scorer_that_raises_is_an_ERROR_not_a_pass_and_not_a_fail():
    def explode(_chunk):
        raise RuntimeError("onnx session is gone")

    result = run_self_test(score=explode, threshold=0.5)

    assert result.status == "error"
    assert "onnx session is gone" in result.detail


def test_an_absent_clip_is_an_ERROR_not_a_pass():
    result = run_self_test(score=_always(0.99), threshold=0.5, clip_path=Path("/nope.wav"))

    assert result.status == "error"
    assert "absent" in result.detail


def test_the_clip_is_driven_through_the_real_conditioning_in_real_chunk_sizes():
    """A stub anywhere in the path would defeat the point: every component
    looked fine on its own while the feature was dead."""
    seen = []

    def spy(chunk: np.ndarray) -> float:
        seen.append(chunk)
        return 0.0

    run_self_test(score=spy, threshold=0.5)

    assert len(seen) > 10, "the clip should yield many 80 ms chunks"
    assert all(c.size == OWW_CHUNK for c in seen), "chunks must be openWakeWord's frame size"
    assert all(c.dtype == np.int16 for c in seen), "int16 is load-bearing — float truncates to 0"
    assert any(int(np.abs(c).max()) > 500 for c in seen), "the clip should carry real speech"


def test_the_shipped_clip_wakes_the_shipped_model():
    """The end-to-end assertion: the REAL model, the REAL clip, the REAL
    conditioning. This is the one that goes red when the service is deaf."""
    model_path = Path(__file__).parents[1] / "models" / "喂Nora.onnx"
    if not model_path.is_file():
        pytest.skip("the wake model is not present")
    from openwakeword.model import Model

    model = Model(wakeword_model_paths=[str(model_path)])

    result = run_self_test(
        score=lambda c: float(list(model.predict(c).values())[0]), threshold=0.5,
    )

    assert result.status == "pass", result.detail
    assert result.peak > 0.5
