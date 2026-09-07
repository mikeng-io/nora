"""`MediaJoin` — `[image a3f2c1]` becomes `[image a3f2c1: …]`, for every
transcript a model is about to read (issue #116).

Tested HERE rather than through one consumer, because the whole point of the
class is that there is one implementation of the decision and six callers of
it. An earlier cut had this logic as a private method on the tag assembler and
its tests written through `assemble`, which is exactly why the other five
transcript builders could ship rendering bare handles without a single test
noticing: the behaviour was pinned to one caller's shape.

`tests/unit/test_media_join_call_sites.py` is the other half — that every
builder actually calls it. This file is about what it DOES.
"""

from __future__ import annotations

from nora.domain.media import MediaObject
from nora.domain.media_join import MediaJoin
from nora.domain.models import DeliveryStatus, TranscriptRow, UnattributedRow
from nora.obs.metrics import Metrics
from nora.testing import FakeMediaObjectStore

SHA = "a3f2c1" + "0" * 58
VOICE_SHA = "b7c2d9" + "0" * 58


def _screen(text: str) -> str | None:
    """The real screen, imported at the test's own layer. Production injects
    `nora.agent.egress.scan_identifiers`; `nora/language/` sits two layers below
    `nora/agent/` and could not import it, which is why the collaborator is
    injected at all — but a test may, and using the REAL one is what keeps this
    suite from pinning a screen production does not run."""
    from nora.agent.egress import scan_identifiers

    return scan_identifiers(text)


def _row(body: str, *, sha: str = SHA, ts: int = 1) -> TranscriptRow:
    media_json = f'[{{"sha256": "{sha}", "media_type": "image/png", "size": 4}}]'
    return TranscriptRow(
        id=ts, sender_id="+852111", sender_name="Miles", ts=ts, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        media_json=media_json,
    )


def _obj(sha: str = SHA, **kw) -> MediaObject:
    base: dict = {
        "sha256": sha, "media_type": "image/png", "size": 4, "source": "inbound",
        "title": "", "description": "", "content": "", "created_at_ms": 1,
    }
    base.update(kw)
    return MediaObject(**base)


def _join(objs=(), *, chars: int = 240, metrics=None) -> MediaJoin:
    return MediaJoin(
        store=FakeMediaObjectStore(objs), screen=_screen,
        metrics=metrics or Metrics(), render_chars=chars,
    )


async def test_a_comprehended_image_renders_its_title_and_description():
    """The short form: `[image a3f2c1: <title> - <description>]`. The stored
    `content` is deliberately NOT here — it is what `media_fetch` serves, and
    putting it in every prompt is the token cost the split exists to avoid."""
    join = _join([_obj(
        title="a plate of noodles",
        description="a bowl of wonton noodles on a marble table",
        content="生記粥麵 $58",
    )])

    got = await join.enrich([_row("睇下 [image a3f2c1]")])

    assert got[0].body == (
        "睇下 [image a3f2c1: a plate of noodles - a bowl of wonton noodles "
        "on a marble table]"
    )


async def test_an_uncomprehended_image_renders_the_bare_handle():
    """R-51's render half. Nothing has read those bytes yet — which is exactly
    the state every medium was in before this existed — so the placeholder
    claims nothing. Never `[image a3f2c1: ]`.

    Positive control: the SAME row with a comprehension on it renders the
    detail (the test above), so this is not passing because the join never
    ran."""
    got = await _join([_obj()]).enrich([_row("睇下 [image a3f2c1]")])

    assert got[0].body == "睇下 [image a3f2c1]"


async def test_a_voice_note_renders_its_whole_transcript_and_is_never_clipped():
    """Issue #116 decision 4, and the explicit exception in the render bound.
    A voice note is conversation CONTENT — collapsing it to title+description
    would mean Nora stops hearing what people actually said and starts reading
    a summary of it. The clip that binds every other medium does not apply."""
    words = "喂" * 400
    join = _join([_obj(
        VOICE_SHA, media_type="audio/aac", title="asking about tonight",
        description="a voice note about dinner", content=words,
    )], chars=40)

    got = await join.enrich(
        [_row("[voice note b7c2d9]", sha=VOICE_SHA)])

    assert got[0].body == f"[voice note b7c2d9: {words}]"


async def test_every_other_mediums_detail_is_bounded():
    """The STORED comprehension is unbounded on purpose — `media_fetch` is what
    serves the rest — so the bound has to be at the render, where the tokens
    are actually spent."""
    got = await _join([_obj(description="x" * 500)], chars=40).enrich(
        [_row("[image a3f2c1]")])

    assert got[0].body == "[image a3f2c1: " + "x" * 40 + "]"


async def test_the_coverage_caveat_survives_the_bound():
    """**The caveat is appended AFTER the clip, and that is the whole reason it
    is a separate field.** Glued onto the description it was clipped off
    exactly when the description was rich — a confident-sounding partial read
    of a twenty-minute clip with its «I have not seen the rest» cut mid-word,
    on the one surface issue #117 exists for."""
    join = _join([_obj(
        title="a race", description="y" * 500,
        provenance={"media_caveat": "(I only watched the first 4 of them.)"},
    )], chars=40)

    got = await join.enrich([_row("[image a3f2c1]")])

    assert got[0].body.endswith("(I only watched the first 4 of them.)]")
    assert "y" * 20 in got[0].body, "the description is still rendered"
    assert "y" * 500 not in got[0].body, "and still clipped"


async def test_content_is_the_fallback_when_a_read_produced_no_headline():
    """A video read with no `comprehension_text_model` (the shipped default)
    has a full timecoded window run and no synthesized title. Without this
    fallback the row is `comprehended` — so the job never retries it — and
    reaches the prompt as nothing at all: the read happened, cost ten
    video-model calls, and was invisible."""
    got = await _join([_obj(
        media_type="video/mp4", content="0:00 — a car pulls away")]).enrich(
        [_row("[video a3f2c1]")])

    assert got[0].body == "[video a3f2c1: 0:00 — a car pulls away]"


async def test_an_identifier_in_a_comprehension_costs_the_detail_not_the_row():
    """R-18's floor, applied at RENDER rather than at store time — and that is
    the stricter position, not a relaxation. `media_objects` is a durable cache
    keyed by sha256 and shared across rooms and restarts, so a row screened
    once at write time would be served unscreened for the life of the
    deployment (`nora/ingest/stickers.py`'s own rule about cache hits)."""
    metrics = Metrics()
    join = _join([_obj(
        title="a business card", description="a card reading +85212345678")],
        metrics=metrics)

    got = await join.enrich([_row("[image a3f2c1]")])

    assert got[0].body == "[image a3f2c1]"
    assert "85212345678" not in got[0].body
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1


async def test_a_store_outage_leaves_the_rows_exactly_as_stored():
    """Fail-soft: a thinner prompt beats no reply. The handles are what the row
    actually says, so degrading to them claims nothing untrue."""
    join = MediaJoin(
        store=FakeMediaObjectStore(fail=True), screen=_screen,
        metrics=Metrics(),
    )

    got = await join.enrich([_row("睇下 [image a3f2c1]")])

    assert got[0].body == "睇下 [image a3f2c1]"


async def test_a_store_that_raises_costs_the_join_and_not_the_turn():
    """The port promises never to raise and its contract suite pins that for
    both implementations, so this is belt for those braces — but the join runs
    on a reply's critical path, and a fake or a future implementation breaking
    the promise must cost a thinner prompt rather than the reply."""

    class _Explodes(FakeMediaObjectStore):
        async def get(self, *, sha256: str):
            raise RuntimeError("db on fire")

    join = MediaJoin(store=_Explodes(), screen=_screen, metrics=Metrics())

    got = await join.enrich([_row("[image a3f2c1]")])

    assert got[0].body == "[image a3f2c1]"


async def test_a_handle_a_sender_typed_is_never_resolved():
    """**The scope rule.** A handle is six hex characters and a body is
    sender-supplied, so resolving `[image a3f2c1]` out of prose would let a
    member type another room's handle and have its description rendered into
    this one. `media_json` is written by the custody stage from bytes Nora
    actually held for THAT row, and it is the only thing the join keys on.

    Positive control: the same body WITH the custody column renders the detail
    (the first test in this file)."""
    forged = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Miles", ts=1,
        body="look at [image a3f2c1]", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        media_json=None,        # nothing was ever held for this row
    )

    got = await _join([_obj(title="somebody else's photo")]).enrich([forged])

    assert got[0].body == "look at [image a3f2c1]"


async def test_a_bracket_in_the_senders_own_text_is_not_a_placeholder():
    """The join rewrites by REGEX over the closed label set plus fixed-width
    hex, never by position, for `body_with_handles`' reason: the text around a
    placeholder is sender-supplied and may contain brackets, and a positional
    replacement would silently corrupt somebody's message."""
    got = await _join([_obj(title="a screenshot")]).enrich(
        [_row("who wrote [image] here [image a3f2c1]")])

    assert got[0].body == "who wrote [image] here [image a3f2c1: a screenshot]"


async def test_it_joins_an_unattributed_row_too():
    """Lazy adoption's row type. These rows are FED TO HONCHO, and on master a
    photo's description and a voice note's words rode in `body` — so a
    back-filled row reached memory with them. `UnattributedRow` gained
    `media_json` for exactly this."""
    row = UnattributedRow(
        id=1, platform="signal", sender_id="+852111", ts=1,
        body="睇下 [image a3f2c1]", conversation_id="g1",
        media_json=f'[{{"sha256": "{SHA}", "media_type": "image/png", "size": 4}}]',
    )

    got = await _join([_obj(title="a plate of noodles")]).enrich([row])

    assert got[0].body == "睇下 [image a3f2c1: a plate of noodles]"
    assert isinstance(got[0], UnattributedRow), "the row type survives"


async def test_the_cost_is_measured_only_when_the_join_actually_ran():
    """«Instruments measure the RESULT; something must measure the COST.»
    `design/19` §10 names this join as what would reopen the decision — one
    sequential `get` per distinct blob, now on six paths — and a reopening
    condition nothing can read is a hope. A window with no media must not
    dilute it with zeros."""
    metrics = Metrics()
    join = _join([_obj(title="a cat")], metrics=metrics)

    plain = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Miles", ts=1, body="hello",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    # A prometheus histogram pre-declares its samples, so the series EXISTS
    # before anything observes it — the reading is the count, not the name.
    await join.enrich([plain])
    assert "nora_media_blobs_joined_count 0.0" in metrics.render()

    await join.enrich([_row("[image a3f2c1]")])
    assert "nora_media_blobs_joined_count 1.0" in metrics.render()
