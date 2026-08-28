"""R-85 (DEC-148) — the send-path misattribution guard.

`nora.agent.attribution` is the mechanical predicate; `test_guards_deliverer.py`
pins it wired into `Deliverer.deliver` (the guard that actually compels). This
file is the pure-function layer: no strands, no store, no adapter — every case
is a `transcript` tuple and a string in, an answer out.

The window below is shaped after the LIVE incident (2026-08-28, conversation
`MuM4gROdh0/9GIRlOa9ckXgykZMkkNmZquM4gROdh08=`), reconstructed from
`docker exec nora-db-1 psql`, with the real phone numbers and names replaced
by fixture-only stand-ins — the shape that mattered (who said what, and who
the flat denial that triggered the refused reply came from) is preserved
exactly; the group's own words are not committed here.
"""

from __future__ import annotations

from nora.agent.attribution import (
    MIN_QUOTE_CHARS,
    addressee_candidates,
    find_misattributed_quote,
)
from nora.domain.models import DeliveryStatus, TranscriptRow

AVERY = "avery_walsh"    # the tagger — the flat denial is his
GRANT = "grant_carver"  # said "收隊", never denied anything
MILES = "miles_novak"          # said "廢", quote-replying to Grant


def _row(
    row_id: int, *, person_id: str | None, ts: int, body: str,
    sender_id: str | None = None, is_nora: bool = False,
) -> TranscriptRow:
    return TranscriptRow(
        id=row_id, sender_id=sender_id or person_id or "unmapped", sender_name=None,
        ts=ts, body=body, is_nora=is_nora, delivery_status=DeliveryStatus.SENT,
        raw_json=None, person_id=person_id, person_name=None,
    )


# The incident's own shape: Grant says the word she will misattribute,
# Miles says a second word she will also misattribute, Avery says two more
# things (his own, correctly quotable) and then flatly denies having pushed
# anyone — the message the refused reply answers.
INCIDENT_WINDOW = (
    _row(1, person_id=GRANT, ts=1_000, body="收隊"),
    _row(2, person_id=MILES, ts=2_000, body="廢"),
    _row(3, person_id=AVERY, ts=3_000, body="聽晚機會係0"),
    _row(4, person_id=AVERY, ts=4_000, body="大到唔出"),
    _row(5, person_id=None, sender_id="+85200000000", ts=5_000, body="[sticker]"),
    _row(6, person_id=None, ts=6_000, body="Nora 的回覆",
         is_nora=True, sender_id="nora"),
    _row(7, person_id=AVERY, ts=7_000, body="我冇逼架"),  # the flat denial
)


class TestAddresseeCandidates:
    def test_the_tagger_alone_is_the_fallback(self):
        got = addressee_candidates(
            tagger_person_id=AVERY, transcript=INCIDENT_WINDOW, reply_text="喂",
        )
        assert got == frozenset({AVERY})

    def test_no_tagger_and_no_at_mention_is_empty(self):
        got = addressee_candidates(
            tagger_person_id=None, transcript=INCIDENT_WINDOW, reply_text="喂",
        )
        assert got == frozenset()

    def test_an_at_mention_of_an_in_window_participant_is_additive(self):
        named_grant = TranscriptRow(
            id=1, sender_id=GRANT, sender_name=None, ts=1_000, body="收隊",
            is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
            person_id=GRANT, person_name="Grant",
        )
        window = (named_grant, *INCIDENT_WINDOW[1:])
        got = addressee_candidates(
            tagger_person_id=AVERY, transcript=window, reply_text="@Grant 你話嘅",
        )
        assert got == frozenset({AVERY, GRANT})

    def test_an_at_mention_of_nobody_in_the_window_changes_nothing(self):
        got = addressee_candidates(
            tagger_person_id=AVERY, transcript=INCIDENT_WINDOW,
            reply_text="@隨便邊個 你話嘅",
        )
        assert got == frozenset({AVERY})

    def test_nora_own_rows_are_never_at_mention_candidates(self):
        # Nora's row (id 6) carries no person_name in this fixture, so this is
        # also covered by the `person_id` guard — kept as its own case because
        # `is_nora` is the clause that must hold even if a future change gives
        # her rows a display name.
        got = addressee_candidates(
            tagger_person_id=None, transcript=INCIDENT_WINDOW, reply_text="@Nora 嘢",
        )
        assert got == frozenset()


class TestFindMisattributedQuote:
    def test_the_incident_the_guard_exists_to_stop(self):
        # Exactly what shipped: quoting Grant's word while addressing
        # Avery, with nothing else in the reply naming Grant.
        addressee = frozenset({AVERY})
        quote = find_misattributed_quote(
            "冇逼？頭先「收隊」邊個講㗎。", transcript=INCIDENT_WINDOW, addressee=addressee,
        )
        assert quote == "收隊"

    def test_a_quote_the_addressee_actually_said_is_not_refused(self):
        addressee = frozenset({AVERY})
        quote = find_misattributed_quote(
            "頭先你話「聽晚機會係0」喎。", transcript=INCIDENT_WINDOW, addressee=addressee,
        )
        assert quote is None

    def test_a_quote_two_people_said_including_the_addressee_is_not_refused(self):
        # The addressee is IN the speaker set alongside somebody else — fair
        # game, per the module's own scoping (a quote nobody-but-the-addressee
        # said is not a misattribution just because somebody else said it too).
        window = (*INCIDENT_WINDOW, _row(8, person_id=AVERY, ts=8_000, body="收隊"))
        quote = find_misattributed_quote(
            "「收隊」係你講嘅。", transcript=window, addressee=frozenset({AVERY}),
        )
        assert quote is None

    def test_a_quote_nobody_in_the_window_said_is_not_refused(self):
        # Deliberately NOT this predicate's job — see the module docstring.
        # Refusing here would block every accurate reference to something
        # said further back than the window reaches.
        quote = find_misattributed_quote(
            "你頭先話「隨便作嘅嘢」喎。", transcript=INCIDENT_WINDOW,
            addressee=frozenset({AVERY}),
        )
        assert quote is None

    def test_no_resolved_addressee_never_refuses(self):
        # The module's deliberate fail-OPEN branch: cannot tell who this is
        # for, so it checks nothing rather than treating everyone as suspect.
        quote = find_misattributed_quote(
            "冇逼？頭先「收隊」邊個講㗎。", transcript=INCIDENT_WINDOW, addressee=frozenset(),
        )
        assert quote is None

    def test_quoting_noras_own_earlier_words_is_never_a_misattribution(self):
        quote = find_misattributed_quote(
            "我頭先都話咗「Nora 的回覆」喎。", transcript=INCIDENT_WINDOW,
            addressee=frozenset({AVERY}),
        )
        assert quote is None

    def test_a_span_shorter_than_the_floor_is_skipped(self):
        assert MIN_QUOTE_CHARS >= 2
        short = "呀" * (MIN_QUOTE_CHARS - 1)
        window = (_row(1, person_id=GRANT, ts=1_000, body=short),)
        quote = find_misattributed_quote(
            f"「{short}」係邊個講？", transcript=window, addressee=frozenset({AVERY}),
        )
        assert quote is None

    def test_the_second_quote_is_found_when_the_first_is_clean(self):
        addressee = frozenset({AVERY})
        quote = find_misattributed_quote(
            "「聽晚機會係0」你都話埋「收隊」呀。",
            transcript=INCIDENT_WINDOW, addressee=addressee,
        )
        assert quote == "收隊"

    def test_a_reply_with_no_quotes_at_all_is_never_refused(self):
        quote = find_misattributed_quote(
            "冇逼？你自己心知肚明啦。", transcript=INCIDENT_WINDOW,
            addressee=frozenset({AVERY}),
        )
        assert quote is None
