"""`nora.agent.reaction.ReactionPicker`: a GIF with her reply, or nothing.

The fakes answer the way the real ones do, including wrong: the model
returns prose around its JSON, names an id it was never shown, or stalls;
Giphy returns nothing. Every one of those must come back `None`, which is
«send her words as written», and be counted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from nora.agent.reaction import RECENT_LINES, ReactionPicker, render_recent
from nora.domain.media import GifCandidate
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.ports import OutboundAttachment
from nora.obs.metrics import Metrics

NOD = GifCandidate("giphy", "nod-1", "Dog Nodding", "A dog nods slowly", ("nod",), "g")
CLAP = GifCandidate("giphy", "clap-1", "Slow Clap", "Someone claps slowly", ("clap",), "g")


@dataclass
class _Said:
    content: str


class _Model:
    """Answers the first question, then the second, from a script; records
    what it was asked so a test can see the material it was handed."""

    def __init__(self, *answers: str, stall: bool = False, boom: bool = False) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.stall, self.boom = stall, boom

    async def complete(self, *, system: str, prompt: str) -> _Said:
        self.prompts.append(prompt)
        if self.boom:
            raise RuntimeError("provider 502")
        if self.stall:
            await asyncio.sleep(10)
        return _Said(self.answers.pop(0))


class _Prompts:
    def __init__(self, text: str = "# What you are reading\nDecide.") -> None:
        self.text = text

    def get(self, *, applies_to: str = "reaction"):
        assert applies_to == "reaction"
        return SimpleNamespace(doctrine_text=self.text)


class _Gifs:
    def __init__(self, *found: GifCandidate) -> None:
        self.found = found
        self.queries: list[str] = []

    async def search(self, *, query: str) -> tuple[GifCandidate, ...]:
        self.queries.append(query)
        return self.found

    async def fetch(self, *, candidate: GifCandidate) -> OutboundAttachment | None:
        return OutboundAttachment(b"GIF89a", "image/gif", "r.gif")


def _picker(model, gifs=None, prompts=None, timeout=1.0):
    metrics = Metrics()
    return ReactionPicker(
        client=model, prompts=prompts or _Prompts(), gifs=gifs or _Gifs(NOD, CLAP),
        metrics=metrics, timeout_seconds=timeout,
    ), metrics


def _count(metrics: Metrics, outcome: str) -> float:
    return metrics.get("nora_reaction_total", outcome=outcome)


async def test_a_gif_it_judged_and_picked_comes_back_with_its_mode() -> None:
    model = _Model('Sure: {"mode": "with", "query": "dog nodding"}', '{"id": "nod-1"}')
    picker, metrics = _picker(model)
    got = await picker.pick(recent="Chi Ming: 乖", reply="乖你個頭")
    assert got is not None and (got.mode, got.query, got.candidate) == ("with", "dog nodding", NOD)
    assert "nod-1" in model.prompts[1] and "乖你個頭" in model.prompts[0]
    assert _count(metrics, "with") == 1


async def test_none_means_no_gif_and_no_search() -> None:
    gifs = _Gifs(NOD)
    picker, metrics = _picker(_Model('{"mode": "none", "query": ""}'), gifs)
    assert await picker.pick(recent="", reply="10 點開波") is None
    assert gifs.queries == [] and _count(metrics, "none") == 1


async def test_an_id_it_was_never_shown_is_not_sent() -> None:
    picker, metrics = _picker(_Model('{"mode": "with", "query": "slow clap"}', '{"id": "made-up"}'))
    assert await picker.pick(recent="", reply="好嘢") is None
    assert _count(metrics, "no_pick") == 1


async def test_nothing_found_is_no_gif() -> None:
    picker, metrics = _picker(_Model('{"mode": "alone", "query": "salute"}'), _Gifs())
    assert await picker.pick(recent="", reply="收到") is None
    assert _count(metrics, "no_candidates") == 1


async def test_a_query_carrying_an_identifier_never_leaves_for_giphy() -> None:
    gifs = _Gifs(NOD)
    picker, metrics = _picker(_Model('{"mode": "with", "query": "call +85291234567"}'), gifs)
    assert await picker.pick(recent="", reply="打俾佢") is None
    assert gifs.queries == [] and _count(metrics, "bad_query") == 1


async def test_a_stalled_model_costs_the_timeout_and_nothing_else() -> None:
    picker, metrics = _picker(_Model(stall=True), timeout=0.05)
    assert await picker.pick(recent="", reply="係囉") is None
    assert _count(metrics, "timeout") == 1


async def test_a_failing_model_is_no_gif_not_an_exception() -> None:
    picker, metrics = _picker(_Model(boom=True))
    assert await picker.pick(recent="", reply="係囉") is None
    assert _count(metrics, "error") == 1


async def test_a_missing_instruction_asks_nothing() -> None:
    model = _Model('{"mode": "with", "query": "x"}')
    picker, metrics = _picker(model, prompts=_Prompts(text="  "))
    assert await picker.pick(recent="", reply="係囉") is None
    assert model.prompts == [] and _count(metrics, "no_instruction") == 1


def _row(i: int, body: str, *, nora: bool = False) -> TranscriptRow:
    return TranscriptRow(
        id=i, sender_id="nora" if nora else f"+85200{i}", sender_name=f"P{i}", ts=i,
        body=body, is_nora=nora, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


def test_the_picker_reads_only_the_latest_lines_one_per_row() -> None:
    rows = [_row(i, f"line {i}") for i in range(RECENT_LINES + 3)]
    rows.append(_row(99, "slow clap 呢個\n第二行", nora=True))
    rows.append(_row(100, ""))
    got = render_recent(rows).splitlines()
    assert len(got) == RECENT_LINES - 1
    assert got[-1] == "Nora: slow clap 呢個 第二行"
    assert "line 0" not in "\n".join(got)
