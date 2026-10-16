"""The wake-gate state machine with a STUB scorer — no ONNX, no socket.

The scorer is a scripted sequence of scores (the anti-fixture rule: it
answers each chunk with the score the scenario says that chunk earned, not
one constant), so reverting the threshold comparison or the silence-tail
accounting turns a NAMED test red.
"""

import numpy as np
import pytest

from wakegate.audio import OWW_CHUNK
from wakegate.gate import WakeGate

LOUD = np.full(OWW_CHUNK, 10000, dtype=np.int16)   # RMS 10000 — speech
SILENT = np.zeros(OWW_CHUNK, dtype=np.int16)        # RMS 0 — silence


def _gate(scores, **kwargs):
    """A gate whose scorer pops scripted scores; anything past the script
    scores 0 (a gate that keeps scoring high never leaves COLLECTING, which
    is the behaviour the max-ceiling test needs to see)."""
    script = list(scores)
    params = dict(threshold=0.5, silence_tail_chunks=3, max_chunks=10, preroll_chunks=4)
    params.update(kwargs)
    return WakeGate(score=lambda _chunk: script.pop(0) if script else 0.0, **params)


def test_below_threshold_never_collects():
    gate = _gate([0.1, 0.2, 0.49])
    assert all(gate.feed(LOUD) is None for _ in range(3))
    assert not gate.collecting


def test_crossing_threshold_starts_collecting_with_the_preroll_as_head():
    gate = _gate([0.0, 0.0, 0.0, 0.9])
    assert gate.feed(LOUD) is None
    assert gate.feed(LOUD) is None
    assert gate.feed(LOUD) is None
    assert gate.feed(LOUD) is None  # the fire
    assert gate.collecting
    # Utterance completes after 3 silent chunks (silence_tail_chunks=3);
    # its head is the full 4-chunk pre-roll — the wake word itself is IN the
    # audio that goes to STT, not discarded as triggering noise.
    utterance = None
    for _ in range(3):
        utterance = gate.feed(SILENT) or utterance
    assert utterance is not None
    assert utterance.audio.size == (4 + 3) * OWW_CHUNK
    assert utterance.peak_score == 0.9


def test_speech_resets_the_silence_tail():
    gate = _gate([0.9])
    gate.feed(LOUD)  # fire
    sequence = [SILENT, SILENT, LOUD, SILENT, SILENT, SILENT]
    results = [gate.feed(chunk) for chunk in sequence]
    # Only the FINAL silent run of 3 completes the utterance — the loud
    # chunk in the middle restarted the tail.
    assert results[:5] == [None] * 5
    assert results[5] is not None


def test_max_chunks_caps_an_utterance_that_never_goes_quiet():
    gate = _gate([0.0, 0.0, 0.0, 0.9])
    for _ in range(4):
        gate.feed(LOUD)  # the 4th fires; buffer holds the 4-chunk pre-roll
    results = [gate.feed(LOUD) for _ in range(6)]  # 4 + 6 = max_chunks
    assert results[-1] is not None
    assert results[-1].audio.size == 10 * OWW_CHUNK


def test_the_gate_resets_to_listening_after_an_utterance():
    gate = _gate([0.9])
    gate.feed(LOUD)
    for _ in range(3):
        gate.feed(SILENT)
    assert not gate.collecting


def test_a_non_oww_sized_chunk_is_refused():
    gate = _gate([0.9])
    try:
        gate.feed(np.zeros(OWW_CHUNK + 1, dtype=np.int16))
    except ValueError:
        return
    raise AssertionError("a mis-sized chunk must not be scored as audio")


# --- end-of-speech by an injected speech detector (VAD) -------------------
# Under ducked music the RMS floor (~1000) never drops under 400, so an RMS-only
# gate keeps collecting and swallows a second 「喂 Nora」. With a speech detector
# injected, LOUD-but-not-speech chunks are quiet and end the utterance.


def _gate_with_speech(scores, speech_script, **kwargs):
    speech = list(speech_script)
    return _gate(scores, speech=lambda _chunk: speech.pop(0) if speech else False, **kwargs)


def test_loud_non_speech_ends_the_utterance_when_a_speech_detector_is_injected():
    # RMS 10000 on every chunk — an RMS-only gate would never end this (ceiling only).
    gate = _gate_with_speech([0.9], speech_script=[True, False, False, False])
    gate.feed(LOUD)  # fire (pre-roll)
    results = [gate.feed(LOUD) for _ in range(4)]  # speech, then 3 x loud-but-no-speech
    assert results[:3] == [None] * 3
    assert results[3] is not None, "loud music after the last word must not hold the gate open"
    assert not gate.collecting


def test_speech_keeps_collecting_even_when_the_chunk_is_quiet_by_rms():
    # A soft-spoken user (RMS under the 400 fallback) must not be cut off
    # while the detector still hears speech.
    soft = np.full(OWW_CHUNK, 100, dtype=np.int16)
    gate = _gate_with_speech([0.9], speech_script=[True] * 6 + [False] * 3)
    gate.feed(LOUD)
    results = [gate.feed(soft) for _ in range(9)]
    assert results[:8] == [None] * 8
    assert results[8] is not None


def test_a_pause_shorter_than_the_tail_does_not_end_the_utterance():
    # 喂 Nora, breath, 而家幾點: a 2-chunk no-speech gap with tail 3 must not split it.
    script = [True, False, False, True, True, False, False, False]
    gate = _gate_with_speech([0.9], speech_script=script)
    gate.feed(LOUD)
    results = [gate.feed(LOUD) for _ in range(8)]
    assert results[:7] == [None] * 7
    assert results[7] is not None
    assert results[7].audio.size == (1 + 8) * OWW_CHUNK  # pre-roll (1 chunk) + 8: one utterance


def test_without_a_speech_detector_the_gate_still_falls_back_to_rms():
    gate = _gate([0.9])  # speech=None
    gate.feed(LOUD)
    assert all(gate.feed(LOUD) is None for _ in range(5))  # loud = never quiet by RMS
    results = [gate.feed(SILENT) for _ in range(3)]
    assert results[2] is not None


def test_flush_ends_a_collecting_utterance_on_the_owners_clock():
    gate = _gate([0.0, 0.0, 0.9])
    gate.feed(LOUD)
    gate.feed(LOUD)
    gate.feed(LOUD)  # the fire; the source then goes quiet WITHOUT sending chunks
    assert gate.collecting
    utterance = gate.flush()
    assert utterance is not None
    assert utterance.audio.size == 3 * OWW_CHUNK  # the pre-roll, nothing padded
    assert utterance.peak_score == 0.9
    assert not gate.collecting


def test_flush_while_listening_is_a_no_op():
    gate = _gate([0.1])
    gate.feed(LOUD)
    assert gate.flush() is None
    assert not gate.collecting


def test_the_listening_peak_is_reported_and_reset_so_a_silent_gate_is_measurable():
    """A gate that never fires must still say what it is SEEING.

    Armed gates with no fires cannot say whether the model is scoring
    near-misses or scoring nothing, and those need opposite fixes. The peak
    score over a window is the number that separates them.
    """
    scores = iter([0.02, 0.41, 0.07, 0.0, 0.0])
    gate = WakeGate(score=lambda _chunk: next(scores), threshold=0.5)
    chunk = np.zeros(OWW_CHUNK, dtype=np.int16)

    for _ in range(3):
        assert gate.feed(chunk) is None  # nothing crossed 0.5

    # The near-miss is visible even though the gate never fired.
    assert gate.take_listening_peak() == pytest.approx(0.41)
    assert gate.last_score == pytest.approx(0.07)

    # Taking it resets the window, so the next report is about the next
    # window and not a running maximum that can never come back down.
    for _ in range(2):
        gate.feed(chunk)
    assert gate.take_listening_peak() == pytest.approx(0.0)


def test_the_listening_peak_ignores_scores_seen_while_collecting():
    """Every chunk is scored, including during COLLECTING, but only LISTENING
    scores answer "would this have woken her" — the rest are the tail of an
    utterance already being captured."""
    scores = iter([0.9, 0.99, 0.95])
    gate = WakeGate(score=lambda _chunk: next(scores), threshold=0.5, silence_tail_chunks=99)
    chunk = np.zeros(OWW_CHUNK, dtype=np.int16)

    gate.feed(chunk)  # 0.9 -> crosses, LISTENING peak is 0.9
    assert gate.collecting
    gate.feed(chunk)  # 0.99 while COLLECTING — must not raise the peak
    gate.feed(chunk)  # 0.95 while COLLECTING

    assert gate.take_listening_peak() == pytest.approx(0.9)
