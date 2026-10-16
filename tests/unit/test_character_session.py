"""The Character write check's synthesis guard.

An account whose text carries the message numbers it was handed is a record of
messages, not a description; `check_revision` refuses it as `ids_in_body` and
says to keep the numbers in `citations`. A number the session was NOT handed (a
year, a price, a model number) is ordinary text and passes.
"""

from __future__ import annotations

from nora.domain.culture import REVISE_REFUSALS, Citable, check_revision

CITABLE = Citable(messages=frozenset({6888, 7020, 12}))


def test_an_account_carrying_handed_message_numbers_is_refused():
    got = check_revision(body="佢揸車接人 (6888, 7020)", citations=("6888",), citable=CITABLE)
    assert got is not None and got.reason == "ids_in_body" and "citations" in got.detail
    assert "ids_in_body" in REVISE_REFUSALS


def test_a_number_it_was_not_handed_is_ordinary_text():
    assert check_revision(body="佢買咗部 3080 顯示卡，2026 年講到依家",
                          citations=("6888",), citable=CITABLE) is None
    assert check_revision(body="they split 12.5 each", citations=("6888",),
                          citable=CITABLE) is None
