"""`nora.testing.fakes.transcript_rows` — the builder every corpus and harness
seeds a window through.

ONE property is pinned here: the builder can seed NORA'S OWN speech. A builder
that hardcoded `is_nora` to `False` could not put her own earlier line in the
window, and «have I already said this?» is a question a whole class of turns
has to answer; a proactive duplicate is that question answered wrong, with her
own raise sitting unattributed in the transcript.

The flag and not a `sender_name` convention, because `nora.agent.prefill.
_speaker` reads `is_nora` to attribute a row: a row that merely CARRIES the
name "Nora" renders as an ordinary member who happens to be called that, which
is a different input and the wrong one.
"""

from __future__ import annotations

from nora.domain.models import DeliveryStatus
from nora.testing.fakes import transcript_rows


def test_the_builder_can_seed_nora_s_own_speech() -> None:
    """Falsification: hardcode `is_nora=False` in `transcript_rows`."""
    rows = transcript_rows(
        ["聽日拎抽包，同 Chi Ming 傾咗邊度幾點未？"],
        sender_id="", sender_name="", person_id=None, ts_base=1_000_000,
        is_nora=True,
    )

    assert [r.is_nora for r in rows] == [True]


def test_a_seeded_row_is_somebody_else_s_unless_asked() -> None:
    """The default is `False`, asserted rather than assumed: a caller that
    passes no `is_nora` must keep its rows unattributed, and a flipped default
    would silently re-attribute every window in the tree."""
    rows = transcript_rows(
        ["買咗未"], sender_id="+852111", sender_name="Miles", person_id="miles_novak",
        ts_base=1_000_000,
    )

    assert [r.is_nora for r in rows] == [False]
    assert [r.delivery_status for r in rows] == [DeliveryStatus.SENT]
