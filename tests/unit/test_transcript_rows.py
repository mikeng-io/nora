"""`nora.testing.fakes.transcript_rows` — the builder every corpus and harness
seeds a window through.

ONE property is pinned here and it is the one that was missing: the builder can
seed NORA'S OWN speech. `is_nora` was hardcoded `False` until 2026-09-17, so no
case built on this helper could put her own earlier line in the window — and
«have I already said this?» is a question a whole class of turns has to answer.
The proactive duplicate of that day (msgs 5450/5455) is that question answered
wrong, with her own raise sitting unattributed in the transcript.

The flag and not a `sender_name` convention, because `nora.agent.prefill.
_speaker` reads `is_nora` to attribute a row: a row that merely CARRIES the
name "Nora" renders as an ordinary member who happens to be called that, which
is a different input and the wrong one.
"""

from __future__ import annotations

from nora.domain.models import DeliveryStatus
from nora.testing.fakes import transcript_rows


def test_the_builder_can_seed_nora_s_own_speech() -> None:
    """Falsification: hardcode `is_nora=False` in `transcript_rows` again."""
    rows = transcript_rows(
        ["聽日拎抽包，同 Chi Ming 傾咗邊度幾點未？"],
        sender_id="", sender_name="", person_id=None, ts_base=1_000_000,
        is_nora=True,
    )

    assert [r.is_nora for r in rows] == [True]


def test_a_seeded_row_is_somebody_else_s_unless_asked() -> None:
    """The default is unchanged, and it is asserted rather than assumed: every
    caller that predates the flag passes no `is_nora`, and a default that had
    flipped would silently re-attribute every window in the tree."""
    rows = transcript_rows(
        ["買咗未"], sender_id="+852111", sender_name="Miles", person_id="miles_novak",
        ts_base=1_000_000,
    )

    assert [r.is_nora for r in rows] == [False]
    assert [r.delivery_status for r in rows] == [DeliveryStatus.SENT]
