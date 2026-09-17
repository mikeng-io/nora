"""Every prompt builder's WHOLE output, parsed as a document (#292).

**The gap this fills is a level, not a case.** `tests/unit/test_blocks.py` tests
one renderer; `tests/architecture/test_one_block_injector.py` reads the SOURCE
and answers «was a block forgotten»; `tests/test_prefill.py` reads ONE builder's
render with `str.index` and `in`. Nothing reads what the other eight builders
actually emit, and nothing anywhere PARSES a prompt — every existing check is a
substring against a hand-cut window of the text.

A substring cannot see nesting. `'untrusted="true"' in tag` is true of a block
whose marker sits on its own opening tag AND of one where the marker is on some
element that happens to precede it in the same slice; `"</m>" in text` is true
whether the transcript closed one element or seven. The failures #292 exists to
prevent are structural — a body that closes its container, an element opened
and never closed, a marker that lands on the wrong element — and structure is
what a parser reads and a substring does not.

**Not a single rooted document, so every builder's output is wrapped.** A prompt
is a SEQUENCE of sibling blocks with prose between some of them (`assemble`
concatenates; the curator prepends lines; `prefill` writes a clock line). That
is well-formed XML *content*, not a well-formed XML *document*, so `_outline`
wraps it in one synthetic root before parsing. A `ParseError` after that wrap is
a real malformation of the builder's own bytes.

**What the outline is and what it deliberately is not.** It carries element
names, nesting, the `untrusted` marker, whether a framing `note=` is present,
and attribute NAMES — never attribute values and never prose. `test_blocks.py`'s
own warning applies («a table of expected strings goes stale»), and it is
answered by holding no strings: the prose in these prompts is graded by the eval
suites and changes without this file having an opinion, while the shape is the
thing #292 made a property of one type.

**The two classes, read off the render rather than off the source.** Class 1 is
member-authored and carries `untrusted="true"`; class 2 is Nora's own framing
and carries no marker at all. `test_one_block_injector`'s census asserts that
distinction exists in the SOURCE of nine modules. These outlines assert which
side each element actually lands on in the bytes a model receives — including
the three containers that carry no marker while wrapping one that does
(`<turn>`, `<conversation>`, `<note>`), which is a shape no source-level census
can tell apart from a forgotten marker.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import pytest
import yaml

from nora.agent.prefill import build_prefill, build_tag_message
from nora.domain.models import (
    DeliveryStatus,
    NormalizedMessage,
    PersonRef,
    Quote,
    TagContext,
    TranscriptRow,
)
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.engagement.debrief_render import (
    render_debrief_input,
    render_debrief_turn,
    render_rollup_input,
    render_rollup_verdict_input,
    render_verdict_input,
)
from nora.infra.transit.arrivals import Answer, Departure, Direction, describe
from nora.ingest.relevance_gate import render_context
from nora.language.culture import CultureAdaptor
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.note_curator import NoteCuratorAdaptor
from nora.language.proactive import ProactiveAdaptor, WorkspaceRoom
from nora.obs.metrics import Metrics
from nora.skills.loader import SkillStore
from nora.testing import InMemoryNoteStore, InMemoryStandingStore
from nora.testing.fakes import FakeRecordStore
from nora.testing.reminders import FakeReminderStore

HKT = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 9, 8, 23, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
TS = NOW_MS - 3_600_000
WS = "friends"


# ---- reading a prompt ------------------------------------------------------


def parse(text: str) -> ET.Element:
    """The prompt as a tree, under one synthetic root.

    A `ParseError` here is the finding, not a limitation of the test: it means a
    builder emitted bytes that do not close, do not nest, or carry a character
    XML has no representation for. The whole argument for the element form is
    that «the separator has to be something a row's content cannot contain»,
    and a separator that does not parse is not one.

    **The ONE parse in this suite, which is also why the `S314` waiver is one
    line rather than four.** The rule is about untrusted documents and the input
    here is neither: it is this repo's own renderer over this file's own
    fixtures, and the synthetic root is written FIRST, so a `<!DOCTYPE>` in a
    payload is content rather than a declaration and no entity is ever defined.
    `defusedxml` is not a dependency of this project and adding one to satisfy a
    rule whose premise does not hold would be the wrong trade.
    """
    try:
        return ET.fromstring(  # noqa: S314 - our own bytes, rooted before parse
            f"<nora_prompt>{text}</nora_prompt>"
        )
    except ET.ParseError as exc:                        # pragma: no cover - the
        # message carries the offending line, which is what a reader needs.
        raise AssertionError(
            f"the rendered prompt is not well-formed: {exc}\n\n{text}"
        ) from exc


def outline(text: str) -> str:
    """Element names, nesting, marker, framing and attribute NAMES — no values.

    `note` and `untrusted` are collapsed into flags rather than listed among the
    attributes, because they are the two properties `ContextBlock.render` owns
    and the two this file is about; everything else is listed by name so a
    dropped or renamed attribute is visible without pinning what it says.
    """
    lines: list[str] = []

    def walk(element: ET.Element, depth: int) -> None:
        attrs = sorted(set(element.attrib) - {"note", "untrusted"})
        flags = ""
        if element.get("untrusted") is not None:
            flags += f" untrusted={element.get('untrusted')}"
        if element.get("note"):
            flags += " note"
        rest = (" " + " ".join(attrs)) if attrs else ""
        lines.append(f"{'  ' * depth}<{element.tag}{flags}{rest}>")
        for child in element:
            walk(child, depth + 1)

    for child in parse(text):
        walk(child, 0)
    return "\n".join(lines)


def blocks(text: str) -> list[ET.Element]:
    """Every element the injector rendered — the ones carrying a `note=`.

    That attribute is the discriminator rather than a list of names, and it is
    the right one: `ContextBlock.render` writes `note=` unconditionally and
    `render_elements` writes only what its caller's `attrs` returns, so an
    element with a framing clause IS a block and an element without one is a
    row. A new block therefore cannot slip past the assertions below by not
    being named anywhere.
    """
    return [e for e in parse(text).iter() if e.get("note") is not None]


# ---- the fixtures each builder needs ---------------------------------------


def row(
    body: str | None = "去邊食",
    *,
    name: str | None = "Grant",
    rid: int = 1,
    ts: int = TS,
    is_nora: bool = False,
) -> TranscriptRow:
    return TranscriptRow(
        id=rid, sender_id="+85291234567", sender_name=name, ts=ts, body=body,
        is_nora=is_nora, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_name=name,
    )


def tag_context(
    body: str = "去邊食", *, name: str = "Grant", quote: Quote | None = None,
) -> TagContext:
    message = NormalizedMessage(
        "signal", "g1", "group", "+852111", "Miles", TS, "nora 邊間好",
        {}, quote=quote,
    )
    return TagContext(
        "tag-1", message, PersonRef("miles", "Miles", "miles", aliases=("阿Miles",)),
        (row(body, name=name),), ("likes espresso",), 0.0,
        ("nora said a thing",), tagger_card=("asked about dinner",),
        notes=(), standing=None, culture=None, decisions=(),
        # TWO people, because `<person>` is a framed block that repeats — a
        # one-person fixture cannot tell a row that repeats from a block served
        # twice, which is what `test_a_prompt_never_serves_the_same_block_twice`
        # has to get right.
        people_in_window=((name, ("asked about dinner",)),
                          ("Miles", ("was looking for a table",))),
    )


def trace(body: str = "去邊食", *, name: str = "Grant") -> TagTrace:
    return TagTrace(
        tag_id="tag-1", conversation_id="g1",
        messages=({"role": "assistant", "content": [{"text": "weighing it"}]},),
        transcript=(row(body, name=name), row("on my way", name="Nora", rid=2,
                                              ts=TS + 1000, is_nora=True)),
        outcome="send_reply", created_at=TS,
    )


def standing() -> Standing:
    return Standing(
        platform="signal", conversation_id="g1",
        body="They are deciding where to eat; nobody has picked yet.",
        fold_count=3, updated_at=TS,
    )


class _CultureStore:
    async def head(self, *, key: str):
        return None


async def proactive(body: str = "去邊食", *, name: str = "Grant") -> str:
    """The decide pass, with a note open and a room position written.

    `name` and `body` reach this prompt through the note's own fields rather
    than a transcript: `inputs` ignores its batch, so what a member typed
    arrives here only after the candidate pass wrote it into a note.
    """
    notes = InMemoryNoteStore()
    await notes.create(
        workspace=WS, title=name, description=body, uncertainty="where",
        closing_condition="a place is picked", established_in="sig-1",
        body="Cyberport Saturday", established_by="tag:miles",
        now_ms=NOW_MS - 86_400_000, anchor_date=20260912,
    )
    positions = InMemoryStandingStore()
    await positions.write(standing=Standing(
        platform="signal", conversation_id="sig-1",
        body="Dinner Saturday is agreed; where is still open.",
        fold_count=3, updated_at=NOW_MS - 7_200_000,
    ))
    adaptor = ProactiveAdaptor(
        decisions=None, notes=notes, standing=positions,
        reminders=FakeReminderStore(), memory=None,
        rooms_of=lambda key: (
            WorkspaceRoom(platform="signal", conversation_id="sig-1",
                          label="Friends", home=True),
            WorkspaceRoom(platform="discord", conversation_id="1/2",
                          label="Zone 42"),
        ) if key == WS else (),
        people_of=None, roster_index_conclusions=0, roster_index_members=12,
        read_timeout_seconds=5.0, ai_peer="nora", clock=lambda: NOW,
        timezone=HKT, metrics=None, clock_store=None, records=None,
        quiet_seconds=1800.0,
    )
    return await adaptor.inputs(key=WS, batch=())


def _record(body: str, name: str) -> FakeRecordStore:
    store = FakeRecordStore()
    for rid, (sender, text, mine) in enumerate(
        ((None, "969P from Tin Shui Chi", True), (name, body, False)), start=7,
    ):
        store.rows.append({
            "id": rid, "platform": "signal", "conversation_id": "conv-1",
            "sender_id": "nora" if mine else "+852111",
            "sender_name": sender, "person_id": None if mine else "miles",
            "ts": NOW_MS + rid, "body": text, "is_nora": mine,
            "delivery_status": DeliveryStatus.SENT, "raw_json": None,
            "quoted_ts": None, "media_json": "[]",
        })
    return store


async def curator(body: str = "去邊食", *, name: str = "Grant") -> str:
    """One note due for revisit, with evidence AND one of her own sends.

    Both halves matter: `<evidence>` and `<own_sends>` are the pair whose
    markers differ, and a fixture with only one of them cannot see a swap.
    """
    notes = InMemoryNoteStore()
    note = await notes.create(
        workspace=WS, title="Route", description="one line",
        uncertainty="undecided", closing_condition="somebody decides",
        established_in="conv-1", body="as it stands", established_by="tag:miles",
        now_ms=NOW_MS, anchor_date=20260101, anchor_entity_ids=("grant",),
    )
    notes.notes[note.id] = replace(note, revisit_at=NOW_MS - 1)
    adaptor = NoteCuratorAdaptor(
        notes=notes, record=_record(body, name), today=lambda: 20261001,
        max_expired_per_tick=10, max_revisits_per_tick=10,
        evidence_messages_per_note=40, metrics=Metrics(),
        clock=lambda: NOW_MS, discharge_enabled=False, events=None,
        conclusions=None, ai_peer="nora",
    )
    return await adaptor.inputs(key=WS, batch=())


async def candidates(body: str = "去邊食", *, name: str = "Ada") -> str:
    notes = InMemoryNoteStore()
    await notes.create(
        workspace=WS, title="Wedding venue", description="Venue undecided",
        uncertainty="which venue", closing_condition="a venue is booked",
        established_in="conv-0", body="two on the shortlist",
        established_by="tag:miles", now_ms=NOW_MS, anchor_date=20260919,
    )
    adaptor = NoteCandidateAdaptor(
        notes=notes, candidates=notes, workspace_for=lambda _: WS,
        label_for=lambda _: "Zone 42",
        description_for=lambda _: "a games room; 2200 is when people log on",
        metrics=Metrics(),
    )
    return await adaptor.inputs(
        key="discord:g1/c1",
        batch=(TranscriptRow(
            id=41, sender_id="p1", sender_name=name, ts=1_788_804_803_000,
            body=body, is_nora=False, delivery_status=DeliveryStatus.SENT,
            raw_json=None,
        ),),
    )


async def culture(body: str = "去邊食", *, name: str = "Ada") -> str:
    return await CultureAdaptor(culture=_CultureStore()).inputs(
        key="discord/g1", batch=[row(body, name=name)],
    )


def skills(body: str = "when someone asks the way", *, name: str = "navigation") -> str:
    """The installed-skill catalogue. Operator-authored — class 2 throughout —
    and included here for exactly that reason: the adversarial sweep needs a
    builder whose correct answer is «no marker», or it only ever proves that
    blocks which already carry one keep it.

    **The frontmatter is DUMPED, not interpolated.** A naive
    `f"description: {body}"` makes the fixture a test of PyYAML: `&` opens an
    anchor, a line break ends the mapping, and the payload never reaches
    `render_available` at all. `safe_dump` quotes whatever it is handed, so what
    is measured below is the renderer rather than the file format in front of
    it.

    `name` lands in the skill's INSTRUCTIONS, which this block withholds by
    design — progressive disclosure is «`when_to_load` is the whole of what you
    get here» — so the second slot's correct behaviour for this builder is that
    it changes nothing in the output.
    """
    with tempfile.TemporaryDirectory() as directory:
        skill = Path(directory) / "navigation"
        skill.mkdir()
        front = yaml.safe_dump(
            {"name": "navigation", "description": body},
            allow_unicode=True, default_flow_style=False,
        )
        (skill / "SKILL.md").write_text(
            f"---\n{front}---\n\n{name}\n", encoding="utf-8",
        )
        return SkillStore(Path(directory)).render_available()


def arrivals(body: str = "Diverted via Castle Peak Road", *, name: str = "KMB") -> str:
    """`describe`'s one free-text field, which is its only model-facing block.

    The rest of that answer is a typed envelope — numbers, instants and
    destinations off a published route table — so `operator_note` is the whole
    of what this builder contributes to a prompt.
    """
    answer = Answer(
        operator=name, reachable=True, as_of="2026-09-08T23:00:00+08:00",
        directions=(Direction(towards="Tin Shui Chi", departures=(
            Departure(at="2026-09-08T23:05:00+08:00", in_minutes=5,
                      scheduled=False),
        )),),
        remark=body,
    )
    return describe("969P", True, [name], [answer]).get("operator_note", "")


def prefill_header(body: str = "去邊食", *, name: str = "Grant") -> str:
    return build_prefill(
        tag_context(body, name=name), display_name="Nora", context_window=40,
    )[0]["content"][0]["text"]


def prefill_volatile(body: str = "去邊食", *, name: str = "Grant") -> str:
    return build_prefill(
        tag_context(body, name=name), display_name="Nora", context_window=40,
    )[1]["content"][0]["text"]


def tag_message(body: str = "去邊食", *, name: str = "Grant") -> str:
    """The tagging message WITH a resolvable quote, so `<replying_to>` renders.

    That nested block is the one element in the tree a substring test is least
    able to judge — it lives inside another block's BODY, so «is the marker on
    the right element» is a question about nesting rather than about which
    characters are present.
    """
    return build_tag_message(
        tag_context(body, name=name, quote=Quote(
            author_id="+85291234567", text="not this text", quoted_ts=TS,
        )),
        tagger_name="Miles", display_name="Nora",
    )


#: Every converted builder, by the name its outline is asserted under. Keyed on
#: the callable rather than on a module path because what is being measured is
#: the BYTES a builder emits, and the only way to get those is to run it.
#:
#: Each entry takes `(body, name=...)` — one member-authored body and one
#: speaker name — so `test_prompt_adversarial.py` can drive the same nine
#: through the same hostile corpus without a second roster.
BUILDERS = {
    "prefill.header": prefill_header,
    "prefill.volatile": prefill_volatile,
    "prefill.tag_message": tag_message,
    "debrief.turn": lambda body="去邊食", *, name="Grant": render_debrief_turn(
        trace=trace(body, name=name), display_name="Nora", metrics=Metrics(),
        tz=UTC),
    "debrief.verdict": lambda body="去邊食", *, name="Grant": render_verdict_input(
        trace=trace(body, name=name), display_name="Nora", metrics=Metrics(),
        tz=UTC),
    "debrief.fold": lambda body="去邊食", *, name="Grant": render_debrief_input(
        trace=trace(body, name=name), prior=standing(), display_name="Nora",
        metrics=Metrics(), tz=UTC),
    "debrief.rollup": lambda body="去邊食", *, name="Grant": render_rollup_input(
        prior=standing(), rows=(row(body, name=name),), display_name="Nora",
        tz=UTC),
    "debrief.rollup_verdict": (
        lambda body="去邊食", *, name="Grant": render_rollup_verdict_input(
            rows=(row(body, name=name),), display_name="Nora", tz=UTC)
    ),
    "relevance_gate": lambda body="去邊食", *, name="Grant": render_context(
        (row(body, name=name),), (standing().body, TS), now=NOW),
    "proactive": proactive,
    "note_curator": curator,
    "note_candidates": candidates,
    "culture": culture,
    "skills": skills,
    "arrivals": arrivals,
}


async def render(name: str, body: str = "去邊食", *, who: str = "Grant") -> str:
    """One builder's bytes. `await`s the five adaptors and calls the ten pure
    functions, so a caller never has to know which is which."""
    result = BUILDERS[name](body, name=who)
    return await result if hasattr(result, "__await__") else result


# ---- the shapes ------------------------------------------------------------


#: The document each builder emits, as `outline` writes it.
#:
#: **These are OUTLINES, not goldens.** They hold no prose and no attribute
#: value, so the eval-graded wording of any of these prompts can change without
#: touching this dict. What they pin is the thing #292 made structural: which
#: elements exist, what contains what, where the marker is, and that a framing
#: clause is present.
#:
#: Three entries carry an element with NO marker wrapping one that has it —
#: `<turn>`, `<conversation>` and the curator's `<note>`. That is deliberate in
#: all three and each note says so («The messages inside carry their own
#: marker»). It is also the shape no source-level census can distinguish from a
#: marker somebody forgot, which is why it is written down here.
SHAPES = {
    "prefill.header": """
<context_header note>
""",
    "prefill.volatile": """
<self_card untrusted=true note>
<peer_card untrusted=true note person>
<peer_history untrusted=true note person>
<people_in_window untrusted=true note>
  <person note name>
  <person note name>
<group_transcript untrusted=true note>
  <m age from n>
""",
    "prefill.tag_message": """
<tag_message untrusted=true note also_known_as from>
  <replying_to untrusted=true note author>
""",
    "debrief.turn": """
<turn note at outcome>
  <what_happened note>
  <how_to_read_the_transcript note>
  <transcript untrusted=true note>
    <m at from>
    <m at delivery from>
  <how_to_read_reasoning note>
  <reasoning untrusted=true note>
    <step role>
""",
    "debrief.verdict": """
<turn note at outcome>
  <what_happened note>
  <how_to_read_the_transcript note>
  <transcript untrusted=true note>
    <m at from>
    <m at delivery from>
  <how_to_read_reasoning note>
  <reasoning untrusted=true note>
    <step role>
""",
    "debrief.fold": """
<standing_prior note>
<turn note at outcome>
  <what_happened note>
  <how_to_read_the_transcript note>
  <transcript untrusted=true note>
    <m at from>
    <m at delivery from>
  <how_to_read_reasoning note>
  <reasoning untrusted=true note>
    <step role>
""",
    "debrief.rollup": """
<standing_prior note>
<conversation note>
  <how_to_read_the_transcript note>
  <transcript untrusted=true note>
    <m at from>
""",
    "debrief.rollup_verdict": """
<conversation note>
  <how_to_read_the_transcript note>
  <transcript untrusted=true note>
    <m at from>
""",
    "relevance_gate": """
<group_transcript untrusted=true note>
  <m age from n>
<standing untrusted=true note written>
""",
    "proactive": """
<now note>
<home_room note label platform>
<open_notes untrusted=true note>
  <note about n title>
<rooms untrusted=true note>
<decisions untrusted=true note>
""",
    "note_curator": """
<now note>
<note note anchor anchor_is id title>
  <evidence untrusted=true note>
    <m age from n>
  <own_sends note>
    <m age from n>
""",
    "note_candidates": """
<room note label>
<batch untrusted=true note>
  <m at from n room>
<notes untrusted=true note>
  <note about n title>
""",
    "culture": """
<batch untrusted=true note>
  <m from n>
""",
    "skills": """
<available_skills note>
  <skill name when_to_load>
""",
    "arrivals": """
<untrusted untrusted=true note source>
""",
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_the_whole_prompt_parses_and_keeps_its_shape(name: str):
    """THE COMPONENT TEST: one builder, its whole output, read as a tree.

    A regression this catches that nothing else does: a body or a title that
    closes its own container. Every existing check of that is
    `assert "</note>" not in block` — which passes if the content forged
    `</note><note id="9">` and the container closed anyway, because the
    substring it looks for is present exactly once either way. Here the forged
    element would appear in the outline as an extra sibling, and a container
    that failed to close would not parse at all.
    """
    assert outline(await render(name)) == SHAPES[name].strip("\n")


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_every_block_frames_itself_and_declares_its_class(name: str):
    """DERIVED, so a NEW block cannot arrive unclassified.

    The shapes above are a literal and a literal only fails on what somebody
    wrote in it. This walks whatever the builder actually emitted: every
    element carrying a framing clause must carry a non-empty one, and its
    marker must be absent or exactly `true` — never `false`, never `1`, never
    an empty string. `untrusted="false"` would read to a model as a positive
    claim that content is trustworthy, which is not a thing any block in this
    tree is entitled to say.
    """
    served = blocks(await render(name))
    assert served, (
        f"{name} rendered no framed block at all — every assertion below loops "
        "over what it emitted, so an empty render turns them all green by "
        "emptiness (`_DECLARED_BLOCKS`' own finding, one file over)"
    )
    for block in served:
        assert block.get("note", "").strip(), (
            f"<{block.tag}> in {name} carries an empty framing clause — a block "
            "handed to a model without one is a block it decides the meaning of"
        )
        marker = block.get("untrusted")
        assert marker in (None, "true"), (
            f"<{block.tag}> in {name} carries untrusted={marker!r}; the marker "
            "is present or absent, never negated"
        )


#: Elements that hold member-authored content and therefore MUST carry the
#: marker, wherever they appear. Derived-by-name rather than by builder because
#: the question «is this content somebody typed» is a property of the element,
#: not of which prompt it landed in — `<transcript>` means the same thing in
#: the fold and in the rollup.
_MEMBER_AUTHORED = {
    "group_transcript", "transcript", "batch", "evidence", "tag_message",
    "replying_to",
}

#: Elements that wrap a member's words and carry NO marker, each with the
#: reason. This list is the one that must be argued for, and it is short on
#: purpose: every entry is a block whose own `note=` hands the classification
#: down to a child that does carry the marker, or whose content is hers.
_UNMARKED_CONTAINERS = {
    "turn": "the fold's envelope; its `<transcript>` child carries the marker",
    "conversation": "same envelope one path over — «the messages inside carry "
                    "their own marker»",
    "note": "the curator's own note text; `<evidence>` inside it is the room's",
    "own_sends": "her OWN delivered messages — nobody else in the room wrote "
                 "any of them, and every row is pinned to from=\"you\" below",
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_no_member_authored_block_loses_its_marker(name: str):
    """The negative half, and the one that would actually catch a regression.

    `test_the_whole_prompt_parses_and_keeps_its_shape` fails if a marker moves,
    but only because somebody wrote the marker into the outline. This asks the
    question directly and over a set keyed on the ELEMENT: if `<transcript>`
    ever ships without `untrusted="true"` in any of the five prompts that carry
    one, this reddens without anybody having remembered to update a shape.
    """
    served = blocks(await render(name))
    assert served, f"{name} rendered no framed block at all"
    for block in served:
        if block.tag in _MEMBER_AUTHORED:
            assert block.get("untrusted") == "true", (
                f"<{block.tag}> in {name} carries what people typed and has no "
                "untrusted marker"
            )


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_every_row_element_sits_inside_a_framed_block(name: str):
    """No record is ever handed to a model bare.

    A row — `<m>`, a `<note>` index line, `<skill>`, `<person>` — carries no
    `note=` of its own by design: it is a field-bearing record, and its framing
    is its container's. That only holds while it HAS a container. A renderer
    whose block wrapper was dropped — which is exactly what the note curator's
    `<note>` elements were on master before #292, «shipped with no container at
    all» — leaves rows sitting at the top level of a prompt with nothing saying
    what they are.
    """
    root = parse(await render(name))
    parents = {id(child): parent for parent in root.iter() for child in parent}
    for element in root.iter():
        if element is root or element.get("note") is not None:
            continue
        parent = parents[id(element)]
        assert parent is not root and parent.get("note") is not None, (
            f"<{element.tag}> in {name} is a record with no framing block "
            "around it"
        )


async def test_the_unmarked_containers_are_only_ever_these_four():
    """The exemption list can only shrink, and is measured over every builder.

    Same construction as `test_one_block_injector`'s census and for the same
    reason: an element that wraps somebody's words and carries no marker is
    either an argued container or a defect, and the difference is whether
    somebody wrote the argument down. A fifth one appearing here has to be
    argued for rather than merely not noticed.
    """
    unmarked: dict[str, str] = {}
    for name in sorted(BUILDERS):
        root = parse(await render(name))
        for element in root.iter():
            if element is root or element.get("note") is None:
                continue
            if element.get("untrusted") is None and len(element):
                unmarked.setdefault(element.tag, name)
    assert set(unmarked) - {"available_skills", "context_header"} == set(
        _UNMARKED_CONTAINERS
    ), (
        f"the set of blocks that wrap something and carry no marker is "
        f"{sorted(unmarked)}; classify a new one in _UNMARKED_CONTAINERS with "
        "its reason, or give it the marker"
    )


#: The member body the curator fixture puts in the record, distinct from
#: anything the fixture attributes to Nora. Content, not an attribute, because
#: the attribute cannot answer the question — see below.
_MEMBER_SAYS = "係咪真係book咗"


async def test_no_member_row_reaches_the_block_that_has_no_marker():
    """`<own_sends>` is exempt because every row in it is HERS. Pin that.

    This is the exemption's justification made checkable, and the FIRST version
    of this test could not check it: it asserted `from="you"` on every row, and
    `_render_own_sends` hard-codes `who="you"` for the whole block. A member row
    leaking in would be rendered under her name and the assertion would pass —
    the test was reading the renderer's constant, not the filter's output. Found
    by breaking `_own_sends` and watching it stay green.

    So the measure is CONTENT. A member's words appearing inside the one
    transcript container in the tree that tells the model «nobody else in the
    room wrote any of them», attributed to her, under no marker, is the failure;
    and the member's words are the only thing in the render that can distinguish
    it from the correct output.
    """
    rendered = await curator(_MEMBER_SAYS)
    root = parse(rendered)
    assert _MEMBER_SAYS in rendered, (
        "the fixture stopped putting a member's message in the record, so the "
        "string this test looks for could not appear either way"
    )
    sends = [e for e in root.iter() if e.tag == "own_sends"]
    assert sends, "the fixture stopped rendering the block this test is about"
    for block in sends:
        assert block.get("untrusted") is None
        rows = [m for m in block if m.tag == "m"]
        assert rows, "an own_sends block with no rows proves nothing"
        assert _MEMBER_SAYS not in ET.tostring(block, encoding="unicode"), (
            "a member's message reached <own_sends> — the block that carries no "
            "marker because nobody else in the room wrote any of it, and whose "
            "rows are all rendered under her own name"
        )


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_a_prompt_never_serves_the_same_block_twice(name: str):
    """Two blocks of one name in one prompt is two answers to one question.

    `assemble` sorts by `order` and drops empty bodies; nothing in it dedupes,
    so a builder that appends the same `ContextBlock` twice — or a refactor
    that leaves both the old call site and the new one — serves the model two
    `<standing>` blocks and no way to tell which is current. A substring test
    cannot see this at all: both copies satisfy every `in` assertion.

    TOP-LEVEL only. A framed block nested inside another legitimately repeats
    — `<person>` once per speaker, `<note>` once per open question — because at
    that depth it is a row that happens to carry its own framing. What may not
    repeat is a block the assembler placed.
    """
    top = [e.tag for e in parse(await render(name)) if e.get("note") is not None]
    duplicated = {tag for tag in top if top.count(tag) > 1}
    assert not duplicated, f"{name} serves {sorted(duplicated)} more than once"
