"""A retained decision's multiple options survive the canonical ledger grammar."""

from nora.domain.note_body import Shown
from nora.domain.standing_ledger import LedgerEntry
from nora.domain.standing_revise import check_ledger_op
from nora.engagement.stages.debrief import _unit_ops


def test_each_retained_option_line_has_its_record_citation():
    ops = _unit_ops([
        {"kind": "decision", "subject": "generated venue",
         "sections": {"Options": "generated amber\ngenerated blue",
                      "Leaning": "generated amber"}},
    ], prior={}, handle="t101")
    assert len(ops) == 1
    result = check_ledger_op(
        ops[0], None, shown=Shown(turn_handles=frozenset({"t101"})),
        unit="generated-unit", platform="signal",
        conversation_id="generated-room", now_ms=1000, id="generated-entry",
    )
    assert isinstance(result, LedgerEntry), result
    assert "generated amber" in result.body and "generated blue" in result.body
