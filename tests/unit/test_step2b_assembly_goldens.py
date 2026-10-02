"""Step 2b of `design/context-assembly/04-every-occasion.md` §5: note-merge, the
reaction picker and the debrief (with the Standing event fold that shares its
prompt) declare a `_BLOCKS` table and are built through `assemble`, and the
debrief's hand-built prompt text moves out of the stage into its renderer. NOT
ONE BYTE any model reads changes.

**The goldens in `tests/fixtures/context_assembly_step2b_goldens.json` were
captured from `master` at `a6d780d5`, BEFORE any of the three was touched**, over
synthetic fixtures, and are the criterion. What is compared is the whole text
each occasion sends, not a substring of it:

* note-merge: the adaptor's whole `inputs()` text. Two notes and many, with and
  without an anchor, a revision the model must see marked `revised`, a body past
  its excerpt, rooms the operator did and did not label, names carrying
  `[ ] : < & "` and control characters, and the blank prompt a notebook with
  nothing to compare returns.
* the reaction picker: BOTH prompts it sends (the first question's and the
  second's), each as `(system, prompt)`. A first question over an empty window,
  a reply that escapes to nothing, names carrying `[ ] : < & "` (rendered by
  `render_recent`, whose hand-built rows are NOT touched here), candidates whose
  title and description carry markup and non-ASCII.
* the debrief: `render_debrief_input` and `render_rollup_input` over every
  optional block present and absent (no culture, no prior, a prior with and
  without a working set, a culture account, an empty window, a turn with no
  steps, every step kind, a clipped step), AND the `(system, user)` pair the tag
  fold and the Standing event fold actually SEND, through `debrief.run` and
  `standing_event._fold_batch` with a recording client: the fold's call, the
  consolidate retry over the model's own over-long answer, both working-set
  caps, a multi-paragraph objective. The pair is read at the model client, so
  the capture does not depend on where the prompt text is built.

Nothing here regenerates a golden. Changing one is a model-visible change and
belongs in a step that says so.

**One golden value was edited by step 4c, and only its two speaker fields**
(`reaction-names-and-bodies-carrying-markup`, both prompts): `render_recent` now
strips `[ ] :` from a speaker as every other transcript renderer does, so master's
`Zoe [admin]: ...` and `Miles [owner]: Novak: c` read `Zoe admin ...` and `Miles
owner Novak: c`. Every other byte of that case, and every other case here, is
master's. `tests/unit/test_step4c_edge_goldens.py` states the difference.

`test_the_empty_pairs_a_hand_join_kept_are_kept` pins the one thing the
conversion could have changed and does not: `assemble` drops an empty body, and
every one of these three occasions hand-joined its pieces, so a block that is
empty (or escapes to nothing) was rendered as an empty pair. Each such block
opts in with `when_empty="render"`, which is why there is no accepted
difference in this step.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nora.agent import reaction
from nora.agent.reaction import ReactionPicker, render_recent
from nora.domain.culture import CultureRevision
from nora.domain.media import GifCandidate
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.standing import Standing, WorkingSetItem
from nora.domain.tag_trace import TagTrace
from nora.engagement import debrief_render
from nora.engagement.debrief_render import render_debrief_input, render_rollup_input
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import debrief
from nora.ingest.stages import standing_event
from nora.language import note_merge
from nora.obs.metrics import Metrics
from nora.testing import FakeRecordStore, InMemoryNoteStore, InMemoryStandingStore
from nora.testing.culture_store import InMemoryCultureStore
from tests.unit import test_note_merge as merge
from tests.unit.test_reaction import _Gifs

GOLDEN: dict[str, Any] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "context_assembly_step2b_goldens.json")
    .read_text(encoding="utf-8"),
)

CASES: dict[str, Callable[[], Awaitable[Any]]] = {}


def _case(name: str):
    def register(fn: Callable[[], Awaitable[Any]]):
        assert name not in CASES, name
        CASES[name] = fn
        return fn
    return register


#: A name that tries every delimiter a rendered row uses, and markup.
_NASTY = 'Zoe [admin]: <b>"Z" & co'
_CONTROL = "\x01\x08"

# =============================================================================
# note-merge
# =============================================================================


@_case("merge-two-notes-anchored-and-not")
async def _merge_two() -> str:
    store = InMemoryNoteStore()
    await merge._note(store, "Saturday dinner", at=merge.T0 - 3 * merge.DAY, anchor=20260927)
    await merge._note(store, "Neway booking", at=merge.T0 - 2 * merge.HOUR, anchor=None)
    return await merge._adaptor(store).inputs(key=merge.WS, batch=())


@_case("merge-many-notes-two-rooms-one-unlabelled")
async def _merge_many() -> str:
    store = InMemoryNoteStore()
    await merge._note(store, "Saturday dinner", at=merge.T0 - 5 * merge.DAY, anchor=20260920)
    await merge._note(store, "Neway booking", at=merge.T0 - 3 * merge.DAY, room="conv-2")
    await merge._note(store, "Headcount", at=merge.T0 - 2 * merge.DAY, room="signal-grp-9f")
    await merge._note(store, "Taxi share", at=merge.T0 - merge.HOUR, anchor=20260925)
    return await merge._adaptor(store, labels={"conv-1": "Friends"}).inputs(
        key=merge.WS, batch=(),
    )


@_case("merge-a-note-the-model-must-see-marked-revised")
async def _merge_revised() -> str:
    store = InMemoryNoteStore()
    a = await merge._note(store, "Saturday dinner", at=merge.T0 - merge.DAY)
    await merge._note(store, "Neway booking", at=merge.T0 - merge.HOUR)
    clock = merge._Clock(merge.T0)
    adaptor = merge._adaptor(store, clock=clock)
    await merge._run(adaptor)
    await store.append_revision(
        unit=merge.WS, note_id=a.id, body="Ada says Neway.",
        parent_revision_id=a.head_revision_id, established_by="tag:ada",
        now_ms=merge.T0 + merge.HOUR + 1,
    )
    await merge._note(store, "Friday lunch", at=merge.T0 + merge.HOUR + 2)
    clock.now = merge.T0 + 2 * merge.HOUR
    return await adaptor.inputs(key=merge.WS, batch=())


@_case("merge-long-and-nasty-text")
async def _merge_nasty() -> str:
    store = InMemoryNoteStore()
    await merge._note(
        store, f"Plan with {_NASTY}", at=merge.T0 - 2 * merge.DAY, anchor=None,
        body="line one <b>& co</b>\nZoe: [fake] speaker\n" + "long body text " * 80,
    )
    await merge._note(
        store, "Control \x01 in the title", at=merge.T0 - merge.HOUR,
        body=_CONTROL,
    )
    return await merge._adaptor(
        store, labels={"conv-1": f"Room {_NASTY}"},
    ).inputs(key=merge.WS, batch=())


@_case("merge-fewer-than-two-notes-is-blank")
async def _merge_blank() -> str:
    store = InMemoryNoteStore()
    await merge._note(store, "Saturday dinner", at=merge.T0 - merge.DAY)
    return await merge._adaptor(store).inputs(key=merge.WS, batch=())


# =============================================================================
# the reaction picker
# =============================================================================


class _Said:
    def __init__(self, content: str) -> None:
        self.content = content
        self.finish_reason = "stop"


class _Recorder:
    """Answers the first question, then the second, and keeps `(system, prompt)`
    of each call, separately: the system text is the instruction and a join of
    the two would hide where one ends."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    async def complete(self, *, system: str, prompt: str) -> _Said:
        self.calls.append([system, prompt])
        return _Said(self.answers.pop(0))


class _Prompts:
    def __init__(self, text: str) -> None:
        self.text = text

    def get(self, *, applies_to: str = "reaction"):
        return SimpleNamespace(doctrine_text=self.text)


_NOD = GifCandidate("giphy", "nod-1", "Dog Nodding", "A dog nods slowly", ("nod",), "g")
_ODD = GifCandidate(
    "giphy", 'odd-"2"', "Cat <b>& 貓</b>", 'Says "hi"\nthen leaves', ("a", "b"), "g",
)


def _chat(i: int, body: str, *, name: str | None = None, nora: bool = False,
          person: str | None = None) -> TranscriptRow:
    return TranscriptRow(
        id=i, sender_id="nora" if nora else f"+85200{i}",
        sender_name=name if name is not None else f"P{i}", ts=i, body=body,
        is_nora=nora, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_name=person,
    )


async def _pick(
    rows: list[TranscriptRow], reply: str, *gifs: GifCandidate,
    instruction: str = "# What you are reading\nDecide.", query: str = "dog nodding",
) -> list[list[str]]:
    model = _Recorder(
        json.dumps({"mode": "with", "query": query}), json.dumps({"id": gifs[0].id}),
    )
    picker = ReactionPicker(
        client=model, prompts=_Prompts(instruction), gifs=_Gifs(*gifs),
        metrics=Metrics(), timeout_seconds=5.0,
    )
    await picker.pick(recent=render_recent(rows), reply=reply)
    return model.calls


@_case("reaction-both-prompts-ordinary-window")
async def _reaction_plain() -> list[list[str]]:
    return await _pick(
        [_chat(1, "10 點開波", name="Miles"), _chat(2, "我哋去邊睇", name="Grant"),
         _chat(3, "睇下先", nora=True)],
        "乖你個頭", _NOD,
    )


@_case("reaction-an-empty-window")
async def _reaction_empty() -> list[list[str]]:
    return await _pick([], "收到", _NOD)


@_case("reaction-a-window-of-empty-bodies")
async def _reaction_bare_attachments() -> list[list[str]]:
    return await _pick([_chat(1, ""), _chat(2, "   ", name="Miles")], "收到", _NOD)


@_case("reaction-a-reply-that-escapes-to-nothing")
async def _reaction_control_reply() -> list[list[str]]:
    return await _pick([_chat(1, "hello", name="Miles")], _CONTROL, _NOD)


@_case("reaction-names-and-bodies-carrying-markup")
async def _reaction_nasty() -> list[list[str]]:
    return await _pick(
        [_chat(1, "a <b>& b</b> \"q\"\nZoe: fake line", name=_NASTY),
         _chat(2, "c", name="Miles", person="Miles [owner]: Novak"),
         _chat(3, _CONTROL + "x", name=_CONTROL)],
        'reply with <tag> & "quotes"\nand a second line', _NOD,
        query='say "hi"',
    )


@_case("reaction-more-lines-than-it-reads-and-long-bodies")
async def _reaction_long() -> list[list[str]]:
    return await _pick(
        [_chat(i, f"line {i} " + "x" * 400, name=f"P{i % 3}") for i in range(12)],
        "reply " * 200, _NOD,
    )


@_case("reaction-candidates-with-markup-and-non-ascii")
async def _reaction_odd_candidates() -> list[list[str]]:
    return await _pick([_chat(1, "go", name="Miles")], "好嘢", _ODD, _NOD)


@_case("reaction-an-instruction-with-braces-and-newlines")
async def _reaction_instruction() -> list[list[str]]:
    return await _pick(
        [_chat(1, "go", name="Miles")], "ok", _NOD,
        instruction="# Reaction\n\nAnswer {JSON}.\n\nFirst question: ...\nSecond: ...",
    )


# =============================================================================
# the debrief
# =============================================================================

T = 1_756_000_000_000
H = 3_600_000


def _row(i: int, ts: int, name: str | None, body: str | None, *, nora: bool = False,
         quoted: int | None = None, delivery: DeliveryStatus = DeliveryStatus.SENT,
         person: str | None = None) -> TranscriptRow:
    return TranscriptRow(
        id=i, sender_id="+852111", sender_name=name, ts=ts, body=body, is_nora=nora,
        delivery_status=delivery, raw_json=None, quoted_ts=quoted,
        person_name=person if person is not None else name,
    )


def _trace(rows=(), messages=(), outcome="send_reply") -> TagTrace:
    return TagTrace(
        tag_id="tag-1", conversation_id="g1", messages=tuple(messages),
        transcript=tuple(rows), outcome=outcome, created_at=T + 10 * H,
    )


def _ordinary_rows() -> tuple[TranscriptRow, ...]:
    return (
        _row(1, T + 9 * H, "Miles", "去邊食"),
        _row(2, T + 9 * H + 60_000, "Grant", "好呀", quoted=T + 9 * H),
        _row(3, T + 9 * H + 120_000, None, "睇下先", nora=True),
    )


def _nasty_rows() -> tuple[TranscriptRow, ...]:
    return (
        _row(1, T + 9 * H, _NASTY, 'a <b>& b</b> "q"\nZoe: [fake] line'),
        _row(2, T + 9 * H + 1, "Miles", "yes", quoted=T + 8 * H),  # parent outside
        _row(3, T + 9 * H + 2, "Miles", "me", quoted=T + 9 * H + 2),  # itself
        _row(4, T + 9 * H + 3, None, "tried", nora=True, delivery=DeliveryStatus.FAILED),
        _row(5, T + 9 * H + 4, None, "pending", nora=True, delivery=DeliveryStatus.PENDING),
        _row(6, T + 9 * H + 5, _CONTROL, _CONTROL),
        _row(7, 0, "Miles", None),
        _row(8, T + 9 * H + 6, "Grant", "long " * 400),
    )


def _steps_messages() -> tuple[dict, ...]:
    long_result = json.dumps({"results": [
        {"name": f"Restaurant {i}", "url": "https://x.example/%E9%A4%90%E5%BB%B3/" + str(i % 2)}
        for i in range(120)
    ]}, ensure_ascii=False)
    return (
        {"role": "user", "content": [{"text": "hello <b>& co</b>"}]},
        {"role": "assistant", "content": [
            {"text": "I will search. " + "thinking " * 120},
            {"reasoningContent": {"reasoningText": {"text": "private " * 700}}},
            {"toolUse": {"name": "web_search", "input": {
                "q": 'ramen "Kwun Tong"', "pad": "x" * 700,
            }}},
        ]},
        {"role": "user", "content": [
            {"toolResult": {"content": [{"text": long_result}]}},
            {"toolResult": {"content": "a plain string result <i>&</i>"}},
            {"toolResult": {"content": [{"text": "not json {"}, {"image": "x"}]}},
        ]},
        {"role": "assistant", "content": [
            {"toolUse": {"name": "send_reply", "input": {"text": "ok"}}},
            {"unknown": 1}, "not a mapping",
        ]},
        "not a message",
        {"role": "assistant", "content": "a string, not a list"},
    )


_WORKING_SET = (
    WorkingSetItem(what="Menu at <Ramen & Co>: four ramen", source="web_search", found_at=T + 6 * H,
                   bears_on='dinner "Saturday"'),
    WorkingSetItem(what="Second item\nwith a newline", source="ask_memory", found_at=T - 30 * H),
)


def _prior(*, body: str = "Dinner Saturday is agreed; where is still open.\nSecond <b>line</b>.",
           working_set=(), updated=T + 2 * H) -> Standing:
    return Standing(
        platform="signal", conversation_id="g1", body=body, fold_count=3,
        updated_at=updated, last_folded_row_id=2, working_set=tuple(working_set),
    )


def _culture(
    body: str = "They decide by attrition; <never> cancelled & \"quiet\".",
) -> CultureRevision:
    return CultureRevision(
        id="rev-1", culture_key="room-1", body=body, written_at=T - 3 * 24 * H,
        established_by="dream:culture:test",
    )


def _input(**kw: Any) -> str:
    kw.setdefault("trace", _trace(rows=_ordinary_rows()))
    kw.setdefault("prior", None)
    kw.setdefault("display_name", "Nora")
    kw.setdefault("metrics", Metrics())
    kw.setdefault("tz", UTC)
    return render_debrief_input(**kw)


def _rollup(**kw: Any) -> str:
    kw.setdefault("prior", None)
    kw.setdefault("rows", _ordinary_rows())
    kw.setdefault("display_name", "Nora")
    kw.setdefault("now_ms", T + 12 * H)
    kw.setdefault("tz", UTC)
    return render_rollup_input(**kw)


@_case("input-the-bare-turn-no-prior-no-culture")
async def _in_bare() -> str:
    return _input()


@_case("input-prior-without-a-working-set")
async def _in_prior() -> str:
    return _input(prior=_prior())


@_case("input-prior-with-a-working-set-and-a-culture-account")
async def _in_full() -> str:
    return _input(
        prior=_prior(working_set=_WORKING_SET), culture=_culture(),
        trace=_trace(rows=_ordinary_rows(), messages=_steps_messages()),
        since_rows=(_row(0, T + 8 * H, "Ada", "earlier in the stretch"),),
    )


@_case("input-a-culture-account-alone")
async def _in_culture() -> str:
    return _input(culture=_culture())


@_case("input-every-step-kind-clipped-and-escaped")
async def _in_steps() -> str:
    return _input(trace=_trace(rows=_ordinary_rows(), messages=_steps_messages()))


@_case("input-names-and-bodies-carrying-markup")
async def _in_nasty() -> str:
    return _input(trace=_trace(rows=_nasty_rows(), outcome="vetoed"), display_name=_NASTY)


@_case("input-an-empty-window-and-a-turn-with-no-steps")
async def _in_empty() -> str:
    return _input(trace=_trace(outcome="silent"))


@_case("input-a-turn-that-sent-nothing")
async def _in_odd_outcome() -> str:
    return _input(trace=_trace(rows=_ordinary_rows(), outcome="llm_error"))


@_case("input-a-prior-and-a-culture-that-escape-to-nothing")
async def _in_control_prior() -> str:
    return _input(prior=_prior(body=_CONTROL), culture=_culture(_CONTROL))


@_case("input-no-prior-and-a-whitespace-culture")
async def _in_blank_prior() -> str:
    # `Standing` refuses a blank body, so only the culture can be whitespace.
    return _input(culture=_culture("   "))


@_case("input-a-long-prior-and-a-working-set-past-its-ttl")
async def _in_long_prior() -> str:
    return _input(prior=_prior(
        body="long position text " * 120, working_set=_WORKING_SET, updated=T - 40 * 24 * H,
    ))


@_case("rollup-the-bare-stretch")
async def _roll_bare() -> str:
    return _rollup()


@_case("rollup-prior-working-set-and-culture")
async def _roll_full() -> str:
    return _rollup(prior=_prior(working_set=_WORKING_SET), culture=_culture())


@_case("rollup-names-and-bodies-carrying-markup")
async def _roll_nasty() -> str:
    return _rollup(rows=_nasty_rows(), display_name=_NASTY, prior=_prior())


@_case("rollup-an-empty-stretch")
async def _roll_empty() -> str:
    return _rollup(rows=())


@_case("rollup-a-prior-and-a-culture-that-escape-to-nothing")
async def _roll_control() -> str:
    return _rollup(prior=_prior(body=_CONTROL), culture=_culture(_CONTROL))


@_case("rollup-a-long-stretch")
async def _roll_long() -> str:
    return _rollup(rows=tuple(
        _row(i, T + i * 60_000, f"P{i % 4}", f"message {i} " + "word " * 40,
             nora=(i % 5 == 0))
        for i in range(1, 60)
    ))


# --- the pair a fold actually SENDS -------------------------------------------


class _Sent:
    """The fold's `ModelClient`: records `(system, prompt)` per call, kept
    apart, and answers from a script (an over-long position first when a case
    wants the consolidate retry)."""

    model = "test-model"

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.calls: list[list[str]] = []

    async def complete(self, *, system: str, prompt: str, trace_attributes=None):
        self.calls.append([system, prompt])
        text = self.script.pop(0) if self.script else "a position"
        return debrief.Completion(json.dumps({"standing": text}), "stop")


class _Objective:
    def __init__(self, text: str) -> None:
        self.text = text

    def objective_body(self, name: str) -> str:
        return self.text


_OBJECTIVE = "OBJECTIVE-TEXT"
_REAL_SHAPED_OBJECTIVE = (
    "# Your standing\n\nYou keep a short position for this room.\n\n"
    "- Write dates, not «tomorrow».\n- Keep what you CONCLUDED.\n\n"
    'Answer as JSON: {"standing": "...", "working_set": []}\n'
)


async def _tag_fold(
    *, script: tuple[str, ...] = (), trace: TagTrace | None = None,
    prior: Standing | None = None, culture: CultureRevision | None = None,
    max_chars: int = 1200, ws_items: int = 5, ws_chars: int = 600,
    objective: str = _OBJECTIVE,
) -> list[list[str]]:
    store = InMemoryStandingStore()
    if prior is not None:
        await store.write(standing=prior)
    client = _Sent(*script)
    await debrief.run(
        trace=trace if trace is not None else _trace(rows=_ordinary_rows()),
        platform="signal", store=store, client=client,
        objective=_Objective(objective), metrics=Metrics(), display_name="Nora",
        record_store=FakeRecordStore(), max_chars=max_chars, fold_batch=200,
        timeout_seconds=5.0, store_timeout_seconds=5.0, now_ms=T + 10 * H + 1_000,
        tz=UTC, culture=culture, working_set_max_items=ws_items,
        working_set_max_chars=ws_chars,
    )
    return client.calls


@_case("fold-sent-the-bare-turn")
async def _fold_bare() -> list[list[str]]:
    return await _tag_fold()


@_case("fold-sent-prior-working-set-culture-and-steps")
async def _fold_full() -> list[list[str]]:
    return await _tag_fold(
        prior=_prior(working_set=_WORKING_SET), culture=_culture(),
        trace=_trace(rows=_ordinary_rows(), messages=_steps_messages()),
        objective=_REAL_SHAPED_OBJECTIVE,
    )


@_case("fold-sent-the-consolidate-retry")
async def _fold_retry() -> list[list[str]]:
    return await _tag_fold(
        script=("x" * 60, "short"), max_chars=50, prior=_prior(working_set=_WORKING_SET),
    )


@_case("fold-sent-the-retry-over-the-models-own-markup")
async def _fold_retry_markup() -> list[list[str]]:
    return await _tag_fold(
        script=('over <tag> & "quoted"\nsecond line ' + "y" * 80, "short"), max_chars=50,
        trace=_trace(rows=_nasty_rows()), culture=_culture(),
    )


@_case("fold-sent-working-set-caps-of-one-and-fifty")
async def _fold_caps() -> list[list[str]]:
    return await _tag_fold(script=("z" * 90, "ok"), max_chars=60, ws_items=1, ws_chars=50)


@_case("fold-sent-an-empty-window")
async def _fold_empty() -> list[list[str]]:
    return await _tag_fold(trace=_trace(outcome="silent"))


def _wiring(*, objective: str, max_chars: int, ws_items: int, ws_chars: int,
            culture: Any = None) -> tuple[standing_event.Wiring, _Sent]:
    client = _Sent()
    return standing_event.Wiring(
        store=InMemoryStandingStore(), record_store=FakeRecordStore(), client=client,
        objective=_Objective(objective),
        slots=ConversationSlots(maxlen=4, spawn=lambda coro: coro.close()),
        display_name="Nora", fold_batch=200, rollup_max_folds=5,
        timeout_seconds=5.0, store_timeout_seconds=5.0, max_chars=max_chars,
        tz=UTC, culture=culture,
        culture_key=(lambda _p, _c: "room-1") if culture is not None else None,
        working_set_max_items=ws_items, working_set_max_chars=ws_chars,
    ), client


async def _event_fold(
    monkeypatch: pytest.MonkeyPatch, *, rows, prior: Standing | None = None,
    culture: CultureRevision | None = None, max_chars: int = 1200,
    ws_items: int = 5, ws_chars: int = 600, objective: str = _OBJECTIVE,
) -> list[list[str]]:
    reader = None
    if culture is not None:
        reader = InMemoryCultureStore()
        await reader.append(
            key="room-1", body=culture.body, written_at=culture.written_at,
            established_by=culture.established_by,
        )
    wiring, client = _wiring(
        objective=objective, max_chars=max_chars, ws_items=ws_items,
        ws_chars=ws_chars, culture=reader,
    )
    # The fold reads the wall clock once, for the prior's age and `<conversation at>`.
    monkeypatch.setattr(standing_event, "time", SimpleNamespace(time=lambda: (T + 12 * H) / 1000))
    await standing_event._fold_batch(
        prior=prior, rows=rows, key=("signal", "g1"), wiring=wiring, metrics=Metrics(),
    )
    return client.calls


EVENT_CASES: dict[str, Callable[..., Awaitable[Any]]] = {}


def _event_case(name: str):
    def register(fn):
        assert name not in EVENT_CASES, name
        EVENT_CASES[name] = fn
        return fn
    return register


@_event_case("event-fold-sent-the-bare-stretch")
async def _ev_bare(mp) -> list[list[str]]:
    return await _event_fold(mp, rows=_ordinary_rows())


@_event_case("event-fold-sent-prior-working-set-and-culture")
async def _ev_full(mp) -> list[list[str]]:
    return await _event_fold(
        mp, rows=_nasty_rows(), prior=_prior(working_set=_WORKING_SET),
        culture=_culture(), objective=_REAL_SHAPED_OBJECTIVE, ws_items=2, ws_chars=300,
    )


# =============================================================================
# the criterion
# =============================================================================


@pytest.mark.parametrize("name", sorted(CASES))
async def test_the_prompt_is_byte_identical_to_master(name: str):
    assert await CASES[name]() == GOLDEN[name]


@pytest.mark.parametrize("name", sorted(EVENT_CASES))
async def test_the_event_folds_prompt_is_byte_identical_to_master(
    name: str, monkeypatch: pytest.MonkeyPatch,
):
    assert await EVENT_CASES[name](monkeypatch) == GOLDEN[name]


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) | set(EVENT_CASES) == set(GOLDEN)


#: What each golden holds and does not, so «every optional block present and
#: absent» is a checked property of the fixtures and not a claim about them.
def _calls(name: str) -> list[list[str]]:
    return GOLDEN[name]


def test_the_goldens_vary_the_blocks_this_step_is_about():
    assert "<culture " not in GOLDEN["input-the-bare-turn-no-prior-no-culture"]
    assert "no position yet" in GOLDEN["input-the-bare-turn-no-prior-no-culture"]
    assert "no working set yet" in GOLDEN["input-the-bare-turn-no-prior-no-culture"]
    assert "<standing_working_set_prior " in GOLDEN["input-prior-without-a-working-set"]
    assert "<item " in GOLDEN["input-prior-with-a-working-set-and-a-culture-account"]
    assert GOLDEN["input-a-culture-account-alone"].startswith("<culture ")
    assert "<steps " in GOLDEN["input-every-step-kind-clipped-and-escaped"]
    assert "clipped" in GOLDEN["input-every-step-kind-clipped-and-escaped"]
    assert "<conversation " in GOLDEN["rollup-the-bare-stretch"]
    assert "<turn " not in GOLDEN["rollup-the-bare-stretch"]
    assert "<stretch " in GOLDEN["rollup-the-bare-stretch"]
    assert GOLDEN["rollup-prior-working-set-and-culture"].startswith("<culture ")
    # The reaction picker's two prompts, and the second one's third block.
    both = _calls("reaction-both-prompts-ordinary-window")
    assert len(both) == 2
    assert "<gif_candidates " not in both[0][1] and "<gif_candidates " in both[1][1]
    assert both[0][1].endswith("\n\nAnswer the first question.")
    assert both[1][1].endswith("\n\nAnswer the second question.")
    # The consolidate retry's second call, and the model's own answer in it.
    retry = _calls("fold-sent-the-consolidate-retry")
    assert len(retry) == 2 and "Your previous answer was:\n" + "x" * 60 in retry[1][1]
    assert retry[1][0] == retry[0][0]
    assert len(_calls("fold-sent-the-bare-turn")) == 1
    assert len(_calls("event-fold-sent-the-bare-stretch")) == 1


def test_the_empty_pairs_a_hand_join_kept_are_kept():
    """The shapes `assemble` would DROP, each in its golden: a window with no
    rows (an empty `<transcript>`), a turn with no steps (an empty `<steps>`),
    an empty reaction window and a reply that escapes to nothing, a prior and a
    culture that escape to nothing, an empty rollup stretch."""
    empty = GOLDEN["input-an-empty-window-and-a-turn-with-no-steps"]
    assert ">\n\n</transcript>" in empty and ">\n\n</steps>" in empty
    assert ">\n\n</stretch>" in GOLDEN["rollup-an-empty-stretch"]
    window = GOLDEN["reaction-an-empty-window"][0][1]
    assert ">\n\n</recent_chat>" in window
    assert ">\n\n</nora_reply>" in GOLDEN["reaction-a-reply-that-escapes-to-nothing"][0][1]
    control = GOLDEN["input-a-prior-and-a-culture-that-escape-to-nothing"]
    assert ">\n\n</culture>" in control and ">\n\n</standing_prior>" in control
    assert ">\n\n</standing_prior>" in GOLDEN["rollup-a-prior-and-a-culture-that-escape-to-nothing"]
    # A whitespace-only culture is NOT that case: it is absent altogether.
    blank = GOLDEN["input-no-prior-and-a-whitespace-culture"]
    assert "no position yet" in blank and "<culture " not in blank


def test_the_note_merge_goldens_carry_the_blocks_they_vary():
    assert GOLDEN["merge-fewer-than-two-notes-is-blank"] == ""
    two = GOLDEN["merge-two-notes-anchored-and-not"]
    assert two.count("<open_note ") == 2 and two.startswith("<today ")
    assert 'changed="revised"' in GOLDEN["merge-a-note-the-model-must-see-marked-revised"]
    assert "its first 600 characters" in GOLDEN["merge-long-and-nasty-text"]
    assert 'room="room 1"' in GOLDEN["merge-many-notes-two-rooms-one-unlabelled"]


# =============================================================================
# the table is what is walked
# =============================================================================


def _spy(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[Any]:
    """Every block `module` hands to its `assemble`, in call order."""
    seen: list[Any] = []
    real = module.assemble

    def spy(blocks, **kwargs):
        blocks = list(blocks)
        seen.extend(blocks)
        return real(blocks, **kwargs)

    monkeypatch.setattr(module, "assemble", spy)
    return seen


@pytest.mark.parametrize(("module", "cases"), [
    (note_merge, ["merge-two-notes-anchored-and-not"]),
    (reaction, ["reaction-both-prompts-ordinary-window"]),
    (debrief_render, [
        "input-prior-with-a-working-set-and-a-culture-account",
        "rollup-prior-working-set-and-culture",
    ]),
], ids=["note-merge", "reaction", "debrief"])
async def test_every_block_assemble_is_handed_carries_its_rows_order_and_key(
    module: Any, cases: list[str], monkeypatch: pytest.MonkeyPatch,
):
    """The goldens cannot see a dropped `restamp`: a block left at the default
    `order=0` still sorts first, and these tables' orders were the append order
    of the join they replaced, so the bytes agree either way. The table is what
    the model reads only if the blocks that reach `assemble` carry it, so that
    is checked directly, and every row of the table is walked by some case."""
    seen = _spy(monkeypatch, module)
    for name in cases:
        await CASES[name]()
    assert seen
    for block in seen:
        assert (block.order, block.key) == module._ROW[block.name], block.name
    assert {b.name for b in seen} == {name for name, _, _ in module._BLOCKS}
