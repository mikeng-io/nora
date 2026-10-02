"""What the Cognition session's MODEL reads must not tell it that it is observed,
and must not state a cap in words.

**Why the first half.** The shadow week is the evidence the step-4 cut-over is
judged on. A model told «this is the shadow run, nothing you write is committed»
may write less because nothing counts, or more because nothing costs, and the gate
then measures behaviour the model will not show once it is live (an audit of
2026-10-02 found the objective saying exactly that, and the verbs' texts saying
«in this run the write is recorded, not committed»). Its tool results must read
like production's too: the same `status` values, and no refusal that names a
dry run.

**Why the second half.** The design keeps caps in code and never in words
(`design/cognition-loop/00-intent.md` §4, DEC-247: a model told of a cap declines
the work). `wake_at`'s text said «too many pending or too many today» and a
refusal said how many.

**What «no number» means here, exactly.** The checks THIS catalogue owns (`remind`'s
past, too_soon, too_far and pending_ceiling; `wake_at`'s daily_cap, too_many_pending
and horizons) state no digit at all (`test_the_remind_and_wake_refusals_carry_no_digit`).
Elsewhere a digit is allowed only where it is the LIVE verb's own validator wording,
which this path shares: a field's length limit («at most 60 characters», Standing's
4000-character ceiling), or an echo of what the model passed. Those are schema facts
the model must be able to meet, not caps on its work; `_ALLOWED_DIGITS` is the whole
list, and a result with any other digit fails
(`test_the_only_numbers_a_result_carries_...`, which runs the oversize refusals).

**What is scanned** (every string the model can read that this catalogue owns):
the assembled instruction (`DreamerInstructions.for_objective("cognition")`), the
prompt the session renders, every verb's description and parameter descriptions as
Strands hands them to the model, every result a scripted session gets back from
every verb down its ok path and its refusal paths, and every refusal detail the pure
checks can produce. A positive control proves the scan sees the old wording.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-measures.json`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from nora.app.cognition_agent_runner import as_strands_tools
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.domain.cognition_pace import (
    PendingSelfWake,
    check_wake_at,
    check_wake_cancel,
    check_wake_move,
)
from nora.domain.cognition_remind import (
    PendingMirroredRemind,
    RemindContext,
    check_remind,
    check_remind_cancel,
    check_remind_move,
)
from nora.domain.cognition_session import Room
from nora.domain.cognition_write import note_base_refusal
from nora.domain.ports import NoteHead
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.stimuli import InMemoryStimulusStore
from tests.objectives import shipped_objectives
from tests.unit.test_cognition_session_verbs_c import GOOD_BODY, NOTE, NOW, _session, iso

#: Wording that tells the model it is in a shadow / dry / unobserved run.
MODE = re.compile(
    r"shadow|dry[- _]?run|would[- ]?(?:have[- ])?write|not committed|recorded, not|"
    r"recorded and held|held, never|never sent|in this run|mirrored|unobserved|"
    r"side by side|read afterwards|nothing you write",
    re.IGNORECASE,
)
#: A cap or a limit stated in words: a count, a duration, the cooldown.
LIMIT = re.compile(
    r"too many|cooldown|seven days|\b\d+\s*(?:s|secs?|seconds?|mins?|minutes?|hours?|days?)\b|"
    r"\b\d+ self-wakes?\b|last 24 hours",
    re.IGNORECASE,
)


def findings(text: str) -> list[str]:
    return [m.group(0) for m in (*MODE.finditer(text), *LIMIT.finditer(text))]


# What the catalogue said before this fix, quoted from the files at origin/master:
# the scan must flag every line of it.
OLD_OBJECTIVE = (
    "# Everything you write here is recorded and not committed\n\n"
    "This is the shadow run. What you write goes to a record that is read afterwards, "
    "side by side\nwith what the live passes did, and nothing you write is committed or "
    "reaches a room."
)
OLD_VERB_TEXT = (
    "In this run the write is recorded, not committed.",
    "In this run it is recorded and held, never sent.",
    "refused (too soon, too far, no subject, too many pending or too many today)",
    "No sooner than the cooldown from now and no later than seven days.",
    "12 self-wakes were already set for this unit in the last 24 hours",
    "300s is inside the cooldown (300s)",
    "no pending mirrored reminder 'r1'",
)


@pytest.mark.parametrize("old", [OLD_OBJECTIVE, *OLD_VERB_TEXT])
def test_the_scan_flags_the_wording_this_fix_removed(old):
    assert findings(old), old


# -- the surfaces ----------------------------------------------------------------------

class _Spy:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.prompt = ""
        self.tools: list[Any] = []

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.system_prompt, self.prompt, self.tools = system_prompt, prompt, list(tools)
        return CognitionRunResult(stop_reason="end_turn")


async def _rendered() -> _Spy:
    """The system prompt (the REAL objective), the prompt and the tools a session hands
    its agent."""
    spy = _Spy()
    store = InMemoryCognitionDryRunStore()
    stimuli = InMemoryStimulusStore(ledger=store)
    stimuli.seed_wake("u", 1)

    async def none(*args: Any, **kwargs: Any) -> Any:
        return ()

    async def no_head(*args: Any, **kwargs: Any) -> Any:
        return None

    loaded = shipped_objectives().for_objective("cognition")
    runner = CognitionSessionRunner(
        dry_run_store=store, stimulus_store=stimuli, agent_runner=spy,
        home_rooms={"u": Room("signal", "room-a")}, read_tools_for=lambda u, r: [],
        skill_text_for=lambda kinds: "", grounder=EmptyGrounder(),
        doctrine_text=loaded.text, doctrine_sha256=loaded.sha256, open_note_titles=none,
        open_events=none, prior_standing=no_head, pending_self_wakes=none,
        pending_mirrored_reminds=none, session_mode="shadow",
        budget=AdaptorBudget(turns=1, total_tokens=1, output_tokens=1, timeout_seconds=5),
        cooldown_seconds=300, units=("u",), session_timeout_seconds=900.0, max_items=12,
        max_strikes=3,
    )
    view = UnitView(
        unit="u", last_activity_ms=None, last_wake=None,
        pending=(Stimulus(
            id=1, unit="u", kind="message", item="message", ref="messages:1", at_ms=NOW,
            recorded_at_ms=NOW, platform="signal", conversation_id="room-a"),),
    )
    await runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=1)
    return spy


async def test_the_assembled_instruction_does_not_tell_the_model_it_is_in_a_shadow_run():
    spy = await _rendered()
    assert "# When a write is refused" in spy.system_prompt          # the real file was read
    assert findings(spy.system_prompt) == []


async def test_the_rendered_prompt_does_not_tell_the_model_it_is_observed():
    spy = await _rendered()
    assert "agenda" in spy.prompt.lower()                            # the prompt was rendered
    assert findings(spy.prompt) == []


async def test_no_verb_description_or_parameter_description_names_the_mode_or_a_cap():
    spy = await _rendered()
    specs = {t.tool_name: t.tool_spec for t in as_strands_tools(spy.tools)}
    assert {"note_update", "wake_at", "remind", "close"} <= set(specs)   # the verbs were built
    for name, spec in specs.items():
        assert findings(json.dumps(spec)) == [], name


async def _results() -> list[Any]:
    """Every result a scripted session reads back from every verb: the ok path and the
    refusal paths, so the strings are the production code's own."""
    out: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        at = iso(NOW + 3_600_000)
        out.append(await by["close"](item_index=0, reason="written"))     # nothing written yet
        out.append(await by["note_create"](**NOTE, body="## Decided\n- x\n## Open\n- y\n"))
        out.append(await by["note_create"](**NOTE, body=GOOD_BODY, anchor_date="not a date"))
        out.append(await by["note_update"](
            note_id="n1", body=GOOD_BODY, parent_revision_id="old"))
        out.append(await by["note_update"](
            note_id="n2", body=GOOD_BODY, parent_revision_id="rev-0"))
        out.append(await by["note_update"](
            note_id="n2", body=GOOD_BODY, parent_revision_id="rev-1"))
        out.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["999"]))
        out.append(await by["event_revise"](
            event_id="nope", reasoning="told", confidence="high", cited_ids=["101"],
            venue_text="v"))
        out.append(await by["event_set_status"](
            event_id="nope", status="confirmed", reasoning="told", confidence="high",
            cited_ids=["101"]))
        out.append(await by["standing_revise"](where_it_stands=""))
        out.append(await by["standing_revise"](where_it_stands="x" * 9000))     # oversize
        out.append(await by["note_create"](**{**NOTE, "title": "t" * 400}, body=GOOD_BODY))
        out.append(await by["standing_revise"](where_it_stands="fine"))
        out.append(await by["wake_at"](subject_rows=[], at=at, why="x"))
        out.append(await by["wake_at"](subject_rows=["event:1"], at=iso(NOW + 1_000), why="x"))
        out.append(await by["wake_at"](
            subject_rows=["event:1"], at=iso(NOW + 30 * 86_400_000), why="x"))
        ok = await by["wake_at"](subject_rows=["event:1"], at=at, why="x")
        out.append(ok)
        out.append(await by["wake_move"](wake_id="nope", at=at, why="x"))
        out.append(await by["wake_cancel"](wake_id="nope", why="x"))
        out.append(await by["remind"](intent="tell", subject_rows=[], at=at))
        out.append(await by["remind"](intent="shout", subject_rows=["note:1"], at=at))
        out.append(await by["remind"](
            intent="tell", subject_rows=["note:3"], at=iso(NOW - 3_600_000)))
        out.append(await by["remind"](
            intent="tell", subject_rows=["note:3"], at=iso(NOW + 1_000)))
        out.append(await by["remind"](
            intent="tell", subject_rows=["note:3"], at=iso(NOW + 4000 * 86_400_000)))
        out.append(await by["remind"](intent="tell", subject_rows=["note:2"], at=at))
        out.append(await by["remind"](intent="tell", subject_rows=["note:1"], at=at))
        out.append(await by["remind_move"](reminder_id="nope", at=at, why="x"))
        out.append(await by["remind_cancel"](reminder_id="nope", why="x"))
        out.append(await by["close"](item_index=9, reason="no_change"))
        out.append(await by["close"](item_index=0, reason="no_change"))
        out.append(await by["close"](item_index=0, reason="no_change"))

    async def head_of(unit: str, note_id: str) -> NoteHead | None:
        return NoteHead(head_revision_id="rev-1") if note_id == "n2" else None

    waiting = (PendingMirroredRemind(
        reminder_ref="r0", platform="signal", conversation_id="room-a",
        subject_rows=("note:1",), created_by_session_id=None,
    ),)
    await _session(behave, pending_reminds=waiting, runner_kwargs={
        "note_head": head_of, "room_proactive": lambda p, c: True,
        "max_pending_per_requester": 100, "remind_min_lead_seconds": 120,
        "remind_max_horizon_days": 365,
    })
    return out


async def test_no_result_a_verb_returns_names_the_mode_or_a_cap():
    results = await _results()
    reasons = {r.get("reason") for r in results if isinstance(r, dict)}
    # the scan read the refusals it is about, and the ok path
    assert {"stale_head", "not_found", "no_subject", "horizon_too_soon", "horizon_too_far",
            "no_citations", "duplicate_pending", "no_such_item", "no_write_for_item",
            "bad_intent", "past", "too_soon", "too_far", "already_closed"} <= reasons, reasons
    assert any(r == {"status": "ok"} for r in results)
    assert any(r.get("status") == "ok" and "wake_id" in r for r in results)
    for result in results:
        assert findings(json.dumps(result)) == [], result


async def test_every_status_a_verb_returns_is_one_the_live_verb_returns():
    results = await _results()
    assert {r["status"] for r in results} <= {"ok", "refused", "unavailable"}


async def test_every_refusal_says_why_in_words():
    """The objective promises «you are told why, in words»: a refusal or an
    unavailable with no `detail` breaks it (event `not_found` and `close` did)."""
    for result in await _results():
        if result["status"] != "ok":
            assert result.get("detail"), result


class _Down(InMemoryCognitionDryRunStore):
    """A ledger every call of which raises, with a message that would leak."""

    def __getattribute__(self, name: str) -> Any:
        if name in ("record_would_write", "record_pace", "record_remind_mirror") or name.startswith(
            ("pending_", "count_"),
        ):
            async def boom(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("postgresql://nora:hunter2@db.internal/nora: connection refused")
            return boom
        return super().__getattribute__(name)


async def _failures(*, store_down: bool, seams_down: bool) -> list[Any]:
    """Every verb called with a store, or every read seam, that raises."""
    out: list[Any] = []

    async def down(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("postgresql://nora:hunter2@db.internal/nora: connection refused")

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        at = iso(NOW + 3_600_000)
        common = {"reasoning": "told", "confidence": "high", "cited_ids": ["101"]}
        calls = {
            "close": {"item_index": 0, "reason": "no_change"},
            "note_create": dict(**NOTE, body=GOOD_BODY),
            "note_update": {"note_id": "n1", "body": GOOD_BODY, "parent_revision_id": "rev-1"},
            "event_propose": dict(title="dinner", **common),
            "event_revise": dict(event_id="e", venue_text="v", **common),
            "event_set_status": dict(event_id="e", status="confirmed", **common),
            "standing_revise": {"where_it_stands": "fine"},
            "wake_at": {"subject_rows": ["event:1"], "at": at, "why": "x"},
            "wake_move": {"wake_id": "w", "at": at, "why": "x"},
            "wake_cancel": {"wake_id": "w", "why": "x"},
            "remind": {"intent": "tell", "subject_rows": ["note:1"], "at": at},
            "remind_move": {"reminder_id": "r", "at": at, "why": "x"},
            "remind_cancel": {"reminder_id": "r", "why": "x"},
        }
        for verb, args in calls.items():
            out.append((verb, await by[verb](**args)))

    kwargs: dict[str, Any] = {"note_head": down, "requester_pending": down}
    if seams_down:
        kwargs.update(
            pending_self_wakes=down, pending_mirrored_reminds=down, open_events=down,
            open_note_titles=down, prior_standing=down,
        )
    await _session(
        behave, store=_Down() if store_down else None, runner_kwargs=kwargs,
    )
    return out


@pytest.mark.parametrize("store_down,seams_down", [(True, False), (False, True), (True, True)])
async def test_a_store_or_a_read_that_raises_never_reaches_the_model_as_raw_text(
    store_down, seams_down,
):
    for verb, result in await _failures(store_down=store_down, seams_down=seams_down):
        assert isinstance(result, dict), verb
        assert result["status"] in {"ok", "refused", "unavailable"}, (verb, result)
        assert "hunter2" not in json.dumps(result) and "postgresql" not in json.dumps(result), verb
        assert findings(json.dumps(result)) == [], (verb, result)


async def test_the_failure_path_was_really_exercised_for_every_verb():
    got = await _failures(store_down=True, seams_down=True)
    assert len(got) == 13
    unavailable = {verb for verb, r in got if r["status"] == "unavailable"}
    # a verb that refused before it ever touched a store (a bad argument) is not in this
    # set, but each of these reached one and was answered `unavailable`
    assert unavailable == {
        "note_create", "note_update", "event_propose", "event_revise", "event_set_status",
        "standing_revise", "wake_at", "wake_move", "wake_cancel", "remind", "remind_move",
        "remind_cancel",
    }


def test_no_refusal_detail_the_pure_checks_can_produce_names_the_mode_or_a_cap():
    pending_wake = PendingSelfWake(
        wake_ref="w1", subject_rows=("event:1",), created_by_session_id=1,
    )
    pending_remind = PendingMirroredRemind(
        reminder_ref="r1", platform="signal", conversation_id="c",
        subject_rows=("note:1",), created_by_session_id=1,
    )
    details: list[str] = []
    for refusal in (
        check_wake_at(subject_rows=[], at_ms=NOW, now_ms=NOW, cooldown_seconds=300,
                      pending=())[0],
        check_wake_at(subject_rows=["a"], at_ms=NOW + 10_000, now_ms=NOW,
                      cooldown_seconds=300, pending=())[0],
        check_wake_at(subject_rows=["a"], at_ms=NOW + 99 * 86_400_000, now_ms=NOW,
                      cooldown_seconds=300, pending=())[0],
        check_wake_at(subject_rows=["a"], at_ms=NOW + 3_600_000, now_ms=NOW,
                      cooldown_seconds=300, pending=(pending_wake,) * 6, max_self_wakes=5)[0],
        check_wake_at(subject_rows=["a"], at_ms=NOW + 3_600_000, now_ms=NOW,
                      cooldown_seconds=300, pending=(), created_today=99,
                      max_self_wakes_per_day=12)[0],
        check_wake_move(wake_ref="x", at_ms=NOW, now_ms=NOW, cooldown_seconds=300,
                        pending=(), created_by_session_id=1),
        check_wake_move(wake_ref="w1", at_ms=NOW + 1, now_ms=NOW, cooldown_seconds=300,
                        pending=(pending_wake,), created_by_session_id=1),
        check_wake_move(wake_ref="w1", at_ms=NOW + 99 * 86_400_000, now_ms=NOW,
                        cooldown_seconds=300, pending=(pending_wake,),
                        created_by_session_id=1),
        check_wake_move(wake_ref="w1", at_ms=NOW + 3_600_000, now_ms=NOW,
                        cooldown_seconds=300, pending=(pending_wake,),
                        created_by_session_id=2),
        check_wake_cancel(wake_ref="x", pending=(), created_by_session_id=1),
        check_wake_cancel(wake_ref="w1", pending=(pending_wake,), created_by_session_id=2),
        check_remind(subject_rows=[], intent="tell", platform="s", conversation_id="c",
                     pending=()),
        check_remind(subject_rows=["a"], intent="x", platform="s", conversation_id="c",
                     pending=()),
        check_remind(subject_rows=["note:1"], intent="tell", platform="signal",
                     conversation_id="c", pending=(pending_remind,)),
        check_remind(subject_rows=["a"], intent="tell", platform="s", conversation_id="c",
                     pending=(), context=RemindContext(room_proactive=False)),
        check_remind(subject_rows=["a"], intent="tell", platform="s", conversation_id="c",
                     pending=(), context=RemindContext(abduced_subject=True)),
        *(
            check_remind(subject_rows=["a"], intent="tell", platform="s", conversation_id="c",
                         pending=(), context=RemindContext(
                             now_ms=NOW, at_ms=NOW + delta, min_lead_seconds=120,
                             max_horizon_days=365))
            for delta in (-1, 1_000, 4000 * 86_400_000)
        ),
        check_remind(subject_rows=["a"], intent="tell", platform="s", conversation_id="c",
                     pending=(pending_remind,) * 3, context=RemindContext(max_pending=3)),
        check_remind_move(reminder_ref="x", pending=(), created_by_session_id=1),
        check_remind_move(reminder_ref="r1", pending=(pending_remind,),
                          created_by_session_id=2),
        check_remind_cancel(reminder_ref="x", pending=(), created_by_session_id=1),
    ):
        assert refusal is not None
        details.append(refusal.detail)
    for base in (
        note_base_refusal(parent_revision_id="a", head_revision_id=None, retired=False),
        note_base_refusal(parent_revision_id="a", head_revision_id="a", retired=True),
        note_base_refusal(parent_revision_id="a", head_revision_id="b", retired=False),
    ):
        assert base is not None
        details.append(base[1])
    assert len(details) == 26 and all(details)
    for detail in details:
        assert findings(detail) == [], detail


#: Digits a refusal may carry. This is the precise claim, and it is narrower than «no
#: number anywhere»: a number IS allowed where the live verb's own validator says it (a
#: field's length limit, «at most 60 characters», which is a schema fact the model must
#: be able to satisfy, not a cap on its work, and which the live path shares), where the
#: refusal ECHOES an argument the model passed (the item index it named, a message id it
#: cited, a length it overran, the format example of a time), and nowhere else. A count of
#: pending rows, a duration, a horizon or a daily ceiling is never allowed (`LIMIT`, and
#: the no-digit check below).
_ALLOWED_DIGITS = re.compile(
    r"\b\d+[- ]characters?|\b\d+ is not an item|2026-10-03T19:40\+08:00|cites \[\d+\]|\(got \d+\)",
)


async def test_the_only_numbers_a_result_carries_are_a_validators_length_limit_or_an_echo():
    results = await _results()
    oversize = [r for r in results if "characters" in str(r.get("detail"))]
    assert oversize, "the Standing and note oversize refusals were not exercised"
    for result in results:
        detail = str(result.get("detail") or "")
        assert not re.search(r"\d", _ALLOWED_DIGITS.sub("", detail)), result


def test_the_remind_and_wake_refusals_carry_no_digit_at_all():
    """The pure checks' details (`remind` past, too_soon, too_far, pending_ceiling;
    `wake_at` daily_cap, too_many_pending, horizons) state no number whatever: these
    are the caps and the limits the design keeps in code."""
    from nora.domain.cognition_pace import check_wake_at
    from nora.domain.cognition_remind import RemindContext, check_remind
    ctxs = [
        RemindContext(now_ms=NOW, at_ms=NOW - 1, min_lead_seconds=120, max_horizon_days=365),
        RemindContext(now_ms=NOW, at_ms=NOW + 1, min_lead_seconds=120, max_horizon_days=365),
        RemindContext(now_ms=NOW, at_ms=NOW + 9999 * 86_400_000, min_lead_seconds=1,
                      max_horizon_days=365),
        RemindContext(max_pending=7, live_pending=7),
    ]
    details = [
        check_remind(subject_rows=["a"], intent="tell", platform="s", conversation_id="c",
                     pending=(), context=c).detail    # type: ignore[union-attr]
        for c in ctxs
    ]
    wake = [
        check_wake_at(subject_rows=["a"], at_ms=NOW + 3_600_000, now_ms=NOW,
                      cooldown_seconds=300, pending=(), created_today=99,
                      max_self_wakes_per_day=12)[0],
        check_wake_at(subject_rows=["a"], at_ms=NOW + 99 * 86_400_000, now_ms=NOW,
                      cooldown_seconds=300, pending=())[0],
    ]
    details += [w.detail for w in wake if w is not None]
    assert len(details) == 6
    assert not any(re.search(r"\d", d) for d in details), details
    assert not any(MODE.search(d) for d in details), details
