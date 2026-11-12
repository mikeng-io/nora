"""A wake whose judges erred does not stop the later wakes."""

from scripts.cognition_loop.run import stops_chain


def test_a_judge_error_lets_the_next_wake_run():
    assert not stops_chain({"status": "ERROR", "step": None, "error": None})


def test_a_failed_step_stops_the_chain():
    assert stops_chain({"status": "ERROR", "step": "session", "error": "boom"})
