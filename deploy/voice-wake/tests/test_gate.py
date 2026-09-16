"""The wake-gate state machine with a STUB scorer — no ONNX, no socket.

The scorer is a scripted sequence of scores (the anti-fixture rule: it
answers each chunk with the score the scenario says that chunk earned, not
one constant), so reverting the threshold comparison or the silence-tail
accounting turns a NAMED test red.
"""

import numpy as np

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
