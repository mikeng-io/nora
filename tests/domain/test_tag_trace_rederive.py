import pytest

from nora.domain.tag_trace import TagTrace
from nora.domain.tag_trace_rederive import rebuild_state


def test_rebuild_state_is_an_unimplemented_phase_2_stub():
    """Task 6's job is retention, not the fold — `rebuild_state` exists so
    `SqlTagTraceStore.recent()` has somewhere to hand its traces once Phase 2
    (`Standing`) implements the real fold. A clear `NotImplementedError`
    rather than a guessed/empty answer, so nothing downstream mistakes an
    unimplemented fold for a real (if boring) one."""
    trace = TagTrace(
        tag_id="t1", conversation_id="c1", messages=(), transcript=(),
        outcome="send_reply", created_at=1,
    )
    with pytest.raises(NotImplementedError):
        rebuild_state([trace])


def test_rebuild_state_refuses_before_reading_an_empty_sequence_too():
    """No special-cased "empty in, empty out" — an implicit success path here
    would be a second, undocumented behaviour Phase 2 would have to notice and
    remove, not add."""
    with pytest.raises(NotImplementedError):
        rebuild_state([])
