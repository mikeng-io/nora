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
(`<turn>`, `<conversation>`, `<description>`), which is a shape no source-level census
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
from nora.domain.block_types import BLOCK_TYPES
from nora.domain.models import (
    DeliveryStatus,
    NormalizedMessage,
    PersonRef,
    Quote,
    TagContext,
    TranscriptRow,
)
from nora.domain.prompt import one_line
from nora.domain.standing import Standing
from nora.domain.tag_trace import TagTrace
from nora.engagement.debrief_render import (
    render_debrief_input,
    render_debrief_turn,
    render_rollup_input,
)
from nora.infra.transit.arrivals import Answer, Departure, Direction, describe
from nora.ingest.relevance_gate import render_context
from nora.language.culture import CultureAdaptor
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.note_curator import NoteCuratorAdaptor
from nora.language.proactive import ProactiveAdaptor, UnitRoom
from nora.obs.metrics import Metrics
from nora.skills.loader import SkillStore
from nora.testing import (
    InMemoryCultureStore,
    InMemoryNoteStore,
    InMemoryStandingStore,
)
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
    """Element names, nesting and attribute NAMES — no values.

    Names only, so a dropped or renamed attribute is visible without pinning
    what it says. `description` and `fields` appear in the list like any other:
    they are what `ContextBlock.render` writes, and a block that stopped
    writing one has to show up here as a changed line.
    """
    lines: list[str] = []

    def walk(element: ET.Element, depth: int) -> None:
        attrs = sorted(element.attrib)
        rest = (" " + " ".join(attrs)) if attrs else ""
        lines.append(f"{'  ' * depth}<{element.tag}{rest}>")
        for child in element:
            walk(child, depth + 1)

    for child in parse(text):
        walk(child, 0)
    return "\n".join(lines)


def blocks(text: str) -> list[ET.Element]:
    """Every element the injector rendered — the ones carrying a `description=`.

    That attribute is the discriminator rather than a list of names, and it is
    the right one: `ContextBlock.render` writes `description=` unconditionally,
    from the block's declared type, and `render_elements` writes only what its
    caller's `attrs` returns. So an element that says what it is IS a block,
    and an element that does not is a row. A new block therefore cannot slip
    past the assertions below by not being named anywhere.
    """
    return [e for e in parse(text).iter() if e.get("description") is not None]


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
        notes=(), standing=None, decisions=(),
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


async def proactive(body: str = "去邊食", *, name: str = "Grant") -> str:
    """The decide pass, with a description open and a room position written.

    `name` and `body` reach this prompt through the note's own fields rather
    than a transcript: `inputs` ignores its batch, so what a member typed
    arrives here only after the candidate pass wrote it into a note.
    """
    notes = InMemoryNoteStore()
    await notes.create(
        unit=WS, title=name, description=body, uncertainty="where",
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
            UnitRoom(platform="signal", conversation_id="sig-1",
                          label="Friends", home=True),
            UnitRoom(platform="discord", conversation_id="1/2",
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
    """One description due for revisit, with evidence AND one of her own sends.

    Both halves matter: `<evidence>` and `<own_sends>` are the pair whose
    markers differ, and a fixture with only one of them cannot see a swap.
    """
    notes = InMemoryNoteStore()
    note = await notes.create(
        unit=WS, title="Route", description="one line",
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
        unit=WS, title="Wedding venue", description="Venue undecided",
        uncertainty="which venue", closing_condition="a venue is booked",
        established_in="conv-0", body="two on the shortlist",
        established_by="tag:miles", now_ms=NOW_MS, anchor_date=20260919,
    )
    adaptor = NoteCandidateAdaptor(
        notes=notes, candidates=notes, unit_for=lambda _: WS,
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
    # The four policy accessors are REQUIRED since master gave the culture fold
    # its own `<room>` block: without them the adaptor cannot name the room it
    # is reading, which is #161's defect one tier over. `None` for the two store
    # keys keeps this builder to the blocks this file is about.
    return await CultureAdaptor(
        culture=InMemoryCultureStore(),
        unit_for=lambda _k: None,
        standing_key_for=lambda _k: None,
        label_for=lambda _k: "Dinner",
        description_for=lambda _k: None,
    ).inputs(key="discord/g1", batch=[row(body, name=name)])


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
    "debrief.fold": lambda body="去邊食", *, name="Grant": render_debrief_input(
        trace=trace(body, name=name), prior=standing(), display_name="Nora",
        metrics=Metrics(), tz=UTC),
    "debrief.rollup": lambda body="去邊食", *, name="Grant": render_rollup_input(
        prior=standing(), rows=(row(body, name=name),), display_name="Nora",
        tz=UTC, now_ms=NOW_MS),
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
    """One builder's bytes. `await`s the five adaptors and calls the eight pure
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
#: `<turn>`, `<conversation>` and the curator's `<description>`. That is deliberate in
#: all three and each description says so («The messages inside carry their own
#: marker»). It is also the shape no source-level census can distinguish from a
#: marker somebody forgot, which is why it is written down here.
SHAPES = {
    "arrivals": """
<transit_remark description fields source>""",
    "culture": """
<room description fields label>
<room_messages description fields>
  <m from n>""",
    "debrief.fold": """
<standing_prior description fields written>
<standing_working_set_prior description fields>
<turn at description fields outcome>
  <what_happened description>
  <transcript description fields>
    <m at from>
    <m at delivery from>
  <steps description fields>
    <step role>""",
    "debrief.rollup": """
<standing_prior description fields written>
<standing_working_set_prior description fields>
<conversation at description fields>
  <stretch description fields>
    <m at from>""",
    "debrief.turn": """
<turn at description fields outcome>
  <what_happened description>
  <transcript description fields>
    <m at from>
    <m at delivery from>
  <steps description fields>
    <step role>""",
    "note_candidates": """
<room description fields label>
<batch description fields>
  <m at from n room>
<notes description fields>
  <note about n title>""",
    "note_curator": """
<today description>
<note anchor anchor_is description fields id title>
  <evidence description fields>
    <m age from n>
  <own_sends description fields>
    <m age from n>""",
    "prefill.header": """
<context_header description>""",
    "prefill.tag_message": """
<tag_message also_known_as description fields from>
  <replying_to author description fields>""",
    "prefill.volatile": """
<presence description>
<self_card description>
<peer_card description fields person>
<peer_history description fields person>
<people_in_window description>
  <person description fields name>
  <person description fields name>
<group_transcript description fields window>
  <m age from n>""",
    "proactive": """
<now description>
<presence description>
<notes description fields>
  <note about n room title unprompted>
<rooms description>
<decisions days description fields>""",
    "relevance_gate": """
<group_transcript description fields>
  <m age from n>
<standing description fields written>""",
    "skills": """
<available_skills description fields>
  <skill name when_to_load>""",
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_the_whole_prompt_parses_and_keeps_its_shape(name: str):
    """THE COMPONENT TEST: one builder, its whole output, read as a tree.

    A regression this catches that nothing else does: a body or a title that
    closes its own container. Every existing check of that is
    `assert "</description>" not in block` — which passes if the content forged
    `</description><description id="9">` and the container closed anyway, because the
    substring it looks for is present exactly once either way. Here the forged
    element would appear in the outline as an extra sibling, and a container
    that failed to close would not parse at all.
    """
    assert outline(await render(name)) == SHAPES[name].strip("\n")


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_every_block_says_what_it_is(name: str):
    """DERIVED, so a NEW block cannot arrive unclassified.

    The shapes above are a literal and a literal only fails on what somebody
    wrote in it. This walks whatever the builder actually emitted: every block
    must say what it is, and must say it in the words its TYPE declares rather
    than words the call site invented. That second half is the whole point of
    #314 — before `nora.domain.block_types`, 27 of 39 call sites wrote their
    own description inline and four block types described themselves two
    different ways in two different modules, with nothing able to notice.
    """
    served = blocks(await render(name))
    assert served, (
        f"{name} rendered no framed block at all — every assertion below loops "
        "over what it emitted, so an empty render turns them all green by "
        "emptiness (`_DECLARED_BLOCKS`' own finding, one file over)"
    )
    for block in served:
        said = block.get("description", "").strip()
        assert said, (
            f"<{block.tag}> in {name} says nothing about what it is — a block "
            "handed to a model without that is a block it decides the meaning of"
        )
        declared = BLOCK_TYPES.get(block.tag)
        assert declared is not None, (
            f"<{block.tag}> in {name} renders as a block and is declared in no "
            "type; add it to `nora.domain.block_types.BLOCK_TYPES`"
        )
        assert said == one_line(declared.description), (
            f"<{block.tag}> in {name} describes itself as {said!r}, which is "
            f"not what its type declares ({declared.description!r}). A "
            "description belongs to the type, not to the call site."
        )


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_every_row_element_sits_inside_a_framed_block(name: str):
    """No record is ever handed to a model bare.

    A row — `<m>`, a `<note>` index line, `<skill>`, `<person>` — says nothing
    about what it is, by design: it is a field-bearing record, and what it is
    is its container's `description=`. That only holds while it HAS a
    container. A renderer whose block wrapper was dropped — which is exactly
    what the curator's `<note>` elements were on master before #292, «shipped
    with no container at all» — leaves rows sitting at the top level of a
    prompt with nothing saying what they are.
    """
    root = parse(await render(name))
    parents = {id(child): parent for parent in root.iter() for child in parent}
    for element in root.iter():
        if element is root or element.get("description") is not None:
            continue
        parent = parents[id(element)]
        assert parent is not root and parent.get("description") is not None, (
            f"<{element.tag}> in {name} is a record with no framing block "
            "around it"
        )


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_a_row_is_explained_by_its_fields_and_never_by_its_own_prose(
    name: str,
):
    """The other half of the split this branch landed: a row carries DATA.

    A block says what it is; a row's fields are annotated once, on the type,
    and rendered in the block's `fields=`. What a row may not do is describe
    itself — that is the call-site description coming back one level down,
    where nothing checks it against a type at all.
    """
    root = parse(await render(name))
    rows = [
        e for e in root.iter()
        if e is not root and e.get("description") is None
    ]
    for element in rows:
        assert "description" not in element.attrib and "fields" not in element.attrib, (
            f"<{element.tag}> in {name} is a row carrying its own prose; move "
            "it to the annotations of its block's type"
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
    top = [
        e.tag for e in parse(await render(name))
        if e.get("description") is not None
    ]
    duplicated = {tag for tag in top if top.count(tag) > 1}
    assert not duplicated, f"{name} serves {sorted(duplicated)} more than once"


@pytest.mark.parametrize("name", sorted(BUILDERS))
async def test_every_attribute_a_render_emits_is_annotated_by_its_type(name: str):
    """A field with no gloss is a token the model gets to invent a meaning for.

    **Found by hand, twice, because nothing looked.** `<batch>` renders
    `reply_to=` and annotated four of its five attributes — a bare TIMESTAMP,
    beside an `at` glossed as «when it was sent», so the unexplained one reads
    as a second send time. Before that, `title`, `about`, `written`, `person`
    and `name` all reached the model undefined for months, and #292 added five
    more (`at`, `room`, `reply_to`, `delivery`, `role`) without anything
    noticing.

    Derived from the RENDER, which is the half the registry cannot do on its
    own: `BLOCK_TYPES` knows what a type declares and not what a renderer
    emits, and the gap between those two is where every one of these lived.

    `description` and `fields` are excluded because they ARE the annotation.

    **What it does NOT cover, stated because the fixture is the bound.** It can
    only see attributes these fixtures actually produce. `<batch>`'s
    `reply_to` needs a row with a `quoted_ts`, which no fixture here has — so
    the field this test was written after is one it would still miss. A census
    over the registry cannot replace it either (that direction is
    `test_every_declared_type_is_actually_rendered`, one file over); what
    closes the gap is a richer fixture, and until there is one this gate is a
    floor rather than a proof.
    """
    root = parse(await render(name))
    parents = {id(child): parent for parent in root.iter() for child in parent}
    missing: list[str] = []
    for element in root.iter():
        if element is root:
            continue
        # A row's fields are annotated on its CONTAINER; a block's on itself.
        owner = element if element.get("description") is not None else parents[
            id(element)
        ]
        declared = dict(BLOCK_TYPES[owner.tag].annotations)
        missing += [
            f"<{element.tag} {key}=> (declared on <{owner.tag}>)"
            for key in element.attrib
            if key not in {"description", "fields"} and key not in declared
        ]
    assert not missing, (
        f"{name} renders attributes nothing explains: {missing}. Add each to "
        "its block type's annotations in `nora.domain.block_types`."
    )
