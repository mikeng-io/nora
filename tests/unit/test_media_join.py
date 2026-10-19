"""`MediaJoin` — `[image a3f2c1]` becomes `[image a3f2c1: …]`, for every
transcript a model is about to read.

Tested HERE rather than through one consumer, because the class is one
implementation of the decision with EIGHT callers. A test written through one
caller's shape (such as `assemble`) would leave the other transcript builders
able to render bare handles without a single test noticing.

`tests/architecture/test_media_join_reaches_every_transcript.py` is the other
half — that every builder actually calls it, with the builder set derived from
the source rather than hand-listed. This file is about what it DOES.
"""

from __future__ import annotations

from typing import ClassVar

from nora.domain.media import MediaObject, label_agrees
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
    """R-51's render half. Nothing has read those bytes yet, so the placeholder
    claims nothing. Never `[image a3f2c1: ]`.

    Positive control: the SAME row with a comprehension on it renders the
    detail (the test above), so this is not passing because the join never
    ran."""
    got = await _join([_obj()]).enrich([_row("睇下 [image a3f2c1]")])

    assert got[0].body == "睇下 [image a3f2c1]"


async def test_a_voice_note_renders_its_whole_transcript_and_is_never_clipped():
    """The explicit exception in the render bound.
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
    is a separate field.** Glued onto the description it would be clipped off
    exactly when the description was rich — a confident-sounding partial read
    of a twenty-minute clip with its «I have not seen the rest» cut mid-word."""
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
    reaches the prompt as nothing at all: the read happened, cost
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
    actually says, so degrading to them claims nothing untrue.

    It is a DISTINCT branch from an uncomprehended row: `lookup`'s availability
    axis separates an unreachable index from an ordinary miss (`get` answers
    `None` for both), and the counter below is what proves this one took the
    unavailable branch."""
    metrics = Metrics()
    join = MediaJoin(
        store=FakeMediaObjectStore(fail=True), screen=_screen, metrics=metrics,
    )

    got = await join.enrich([_row("睇下 [image a3f2c1]")], path="tag")

    assert got[0].body == "睇下 [image a3f2c1]"
    assert metrics.get(
        "nora_media_join_total", path="tag", status="unavailable",
        reason="store") == 1


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
    """Lazy adoption's row type. These rows are FED TO HONCHO, so
    `UnattributedRow` carries `media_json`: a back-filled row reaches memory
    with a photo's description and a voice note's words, which are not in
    `body`."""
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
    The join costs a `get` per distinct blob on eight paths, and a cost
    nothing can read is a hope. A window with no media must not dilute it with
    zeros."""
    metrics = Metrics()
    join = _join([_obj(title="a cat")], metrics=metrics)

    plain = TranscriptRow(
        id=1, sender_id="+852111", sender_name="Miles", ts=1, body="hello",
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    # LABELLED by path, so prometheus-client emits no child series until one
    # is observed — absence is readable, unlike the unlabelled version where
    # every histogram pre-declares a `_count 0.0`.
    await join.enrich([plain], path="tag")
    assert 'nora_media_blobs_joined_count{path="tag"}' not in metrics.render()

    await join.enrich([_row("[image a3f2c1]")], path="tag")
    assert 'nora_media_blobs_joined_count{path="tag"} 1.0' in metrics.render()

    # BOTH series, and by the caller's OWN path — a hardcoded label would leave
    # adoption's 1000-row backfill and a tag window in one series, where the
    # batch paths (the exact ones where N is not 1) saturate `+Inf` and the tag
    # path is the only thing anybody can read. Asserted on the LATENCY series
    # as well as the count, because they are two separate observations.
    await join.enrich([_row("[image a3f2c1]")], path="adoption")
    rendered = metrics.render()
    assert 'nora_media_join_seconds_count{path="adoption"} 1.0' in rendered
    assert 'nora_media_join_seconds_count{path="tag"} 1.0' in rendered
    assert 'nora_media_blobs_joined_count{path="adoption"} 1.0' in rendered


# ---- guards for behaviours a mutation sweep can break ---------------------


async def test_a_forged_label_cannot_switch_off_the_render_bound():
    """**A sender-controllable render bound.** The `handle -> label` map is
    built from `placeholders_in(row.body)` — sender-supplied text — and
    `voice note` is the one label whose detail renders UNCLIPPED. So a member
    sends a photo of a document (OCR fills `content` with the page), then types
    the literal string `[voice note a3f2c1]` in any later message: the forged
    label wins a last-write-wins map, `media_detail` returns `content` instead
    of `title - description`, and `max_chars=0` renders the whole page into the
    prompt — on eight paths, two of which write to Honcho.
    `comprehension_render_chars` is the only bound on per-attachment prompt
    spend and any member could switch it off with a typed string.

    A claimed label is accepted only when it AGREES with the media type the
    custody stage recorded."""
    page = "PAGE " * 400
    join = _join([_obj(
        media_type="image/png", title="a document",
        description="a scanned page", content=page,
    )], chars=40)

    got = await join.enrich([
        _row("睇下 [image a3f2c1]"),
        _row("[voice note a3f2c1]", ts=2),      # forged, by a later message
    ])

    assert page not in got[0].body
    assert got[0].body == "睇下 [image a3f2c1: a document - a scanned page]"


async def test_a_real_voice_note_label_is_still_honoured():
    """The positive control, and the reason the check is AGREEMENT rather than
    «derive it from the record and ignore the body». `voice note` and `audio`
    are the same top-level media type — the distinction is a separate axis the
    record does not carry — so the body is the only place it survives, and it
    has to be believed when it is consistent."""
    words = "喂" * 400
    join = _join([_obj(
        VOICE_SHA, media_type="audio/aac", title="asking about tonight",
        description="about dinner", content=words,
    )], chars=40)

    got = await join.enrich([_row("[voice note b7c2d9]", sha=VOICE_SHA)])

    assert got[0].body == f"[voice note b7c2d9: {words}]"


async def test_an_unreadable_index_is_unavailable_and_never_nothing_found():
    """**A failing join is counted**, because on the deployment it has no log:
    container logs are not on the docker log driver, so a permanently failing
    join would mean every prompt on eight paths quietly losing its media with
    nothing to read.

    It cannot be counted from `get`: `MediaObjectStore.get` answers
    `None` for a missing row and for an unreachable database alike, which its
    contract suite pins. `lookup` is the port method with the availability axis
    that makes R-27's three states expressible here at all."""
    metrics = Metrics()
    join = MediaJoin(
        store=FakeMediaObjectStore(fail=True), screen=_screen, metrics=metrics)

    got = await join.enrich([_row("睇下 [image a3f2c1]")], path="tag")

    assert got[0].body == "睇下 [image a3f2c1]"
    assert metrics.get(
        "nora_media_join_total", path="tag", status="unavailable",
        reason="store") == 1
    assert metrics.get(
        "nora_media_join_total", path="tag", status="nothing_found",
        reason="") == 0, "an outage is never «nobody has read these bytes»"


async def test_an_unread_window_is_nothing_found_and_not_an_outage():
    """The other side of the same distinction, and the control for it: the
    index answered, and nothing in the window has been comprehended yet. A real
    answer about those bytes, not a failure — and it must not page anybody."""
    metrics = Metrics()
    join = _join([_obj()], metrics=metrics)

    await join.enrich([_row("[image a3f2c1]")], path="tag")

    assert metrics.get(
        "nora_media_join_total", path="tag", status="nothing_found",
        reason="") == 1
    assert metrics.get(
        "nora_media_join_total", path="tag", status="unavailable",
        reason="store") == 0


async def test_one_lookup_serves_a_whole_window():
    """A BOUND to go with the measurement. An unbatched `get` per distinct blob
    is fine for a three-blob tag window but an unbounded number of round trips
    for adoption's 1000-row backfill scan and for the Dreamer's batch."""

    class _Counting(FakeMediaObjectStore):
        calls = 0

        async def lookup(self, *, sha256s):
            _Counting.calls += 1
            return await super().lookup(sha256s=sha256s)

    other = "cc1122" + "0" * 58
    store = _Counting([_obj(title="one"), _obj(other, title="two")])
    join = MediaJoin(store=store, screen=_screen, metrics=Metrics())

    got = await join.enrich(
        [_row("[image a3f2c1]"), _row("[image cc1122]", sha=other, ts=2)])

    assert _Counting.calls == 1, "one round trip for the whole window"
    assert "one" in got[0].body and "two" in got[1].body


async def test_every_blob_in_a_backfill_is_joined_in_bounded_batches():
    """**A cap on distinct blobs is data loss on the one caller that is a backfill.**

    A cap that kept the LAST N distinct blobs would drop the oldest:
    `RecordStore.unattributed` returns rows OLDEST FIRST, adoption feeds each to
    Honcho and then calls `set_person_id` — which is exactly what removes the
    row from every future scan. So an operator adding a person after months of
    chat would have the oldest blobs in the scan go into memory as bare handles
    and be marked adopted, with no second pass ever.

    Batching bounds the same thing without losing anything: each QUERY asks
    about at most `query_blobs`, and the number of queries is bounded by the
    caller's own row limit."""

    class _Counting(FakeMediaObjectStore):
        queries: ClassVar[list[int]] = []

        async def lookup(self, *, sha256s):
            _Counting.queries.append(len(sha256s))
            return await super().lookup(sha256s=sha256s)

    _Counting.queries = []
    shas = [f"{i:06x}" + "0" * 58 for i in range(5)]
    store = _Counting([_obj(sha, title=f"photo {i}") for i, sha in enumerate(shas)])
    join = MediaJoin(
        store=store, screen=_screen, metrics=Metrics(), query_blobs=2)
    rows = [_row(f"[image {sha[:6]}]", sha=sha, ts=i + 1)
            for i, sha in enumerate(shas)]

    got = await join.enrich(rows, path="adoption")

    assert all(": photo" in (r.body or "") for r in got), (
        "a blob past the query bound rendered bare and its row was then "
        "marked adopted — lost for good"
    )
    assert _Counting.queries == [2, 2, 1], "each query is bounded"


async def test_the_oldest_blob_is_joined_too():
    """The direction of a truncation, pinned on its own. `[:n]` and
    `[-n:]` both «bound» the work and they lose opposite ends; with batching
    neither is a choice anybody has to make right."""
    old_sha, new_sha = "aa0001" + "0" * 58, "bb0002" + "0" * 58
    join = MediaJoin(
        store=FakeMediaObjectStore([
            _obj(old_sha, title="the oldest"), _obj(new_sha, title="the newest"),
        ]),
        screen=_screen, metrics=Metrics(), query_blobs=1,
    )

    got = await join.enrich([
        _row("[image aa0001]", sha=old_sha, ts=1),
        _row("[image bb0002]", sha=new_sha, ts=2),
    ])

    assert "the oldest" in got[0].body
    assert "the newest" in got[1].body


async def test_the_blob_count_is_the_true_count():
    """The histogram observes the set BEFORE it is bounded: observed after, it
    could never exceed the cap, and 201 blobs would be indistinguishable from
    5000, which is the number needed to size the very bound its own help text
    says it exists to size."""
    metrics = Metrics()
    shas = [f"{i:06x}" + "0" * 58 for i in range(5)]
    join = MediaJoin(
        store=FakeMediaObjectStore([_obj(sha, title="x") for sha in shas]),
        screen=_screen, metrics=metrics, query_blobs=2,
    )

    await join.enrich(
        [_row(f"[image {sha[:6]}]", sha=sha, ts=i + 1)
         for i, sha in enumerate(shas)],
        path="adoption",
    )

    assert 'nora_media_blobs_joined_sum{path="adoption"} 5.0' in metrics.render()


async def test_a_batch_that_cannot_be_read_refuses_the_whole_window():
    """A partial answer to «what does this window hold» is one a caller renders
    as complete. If any batch comes back unavailable the join degrades whole,
    to the bare handles the rows already carry."""
    metrics = Metrics()

    class _FailsSecond(FakeMediaObjectStore):
        seen = 0

        async def lookup(self, *, sha256s):
            _FailsSecond.seen += 1
            if _FailsSecond.seen == 2:
                from nora.domain.ports import MediaLookup
                return MediaLookup(available=False, reason="store")
            return await super().lookup(sha256s=sha256s)

    shas = [f"{i:06x}" + "0" * 58 for i in range(4)]
    join = MediaJoin(
        store=_FailsSecond([_obj(sha, title="x") for sha in shas]),
        screen=_screen, metrics=metrics, query_blobs=2,
    )

    got = await join.enrich(
        [_row(f"[image {sha[:6]}]", sha=sha, ts=i + 1)
         for i, sha in enumerate(shas)],
        path="adoption",
    )

    assert all(":" not in (r.body or "") for r in got)
    assert metrics.get(
        "nora_media_join_total", path="adoption", status="unavailable",
        reason="store") == 1


async def test_a_hanging_store_costs_the_join_and_not_the_turn():
    """`enrich` catches exceptions; it cannot catch a HANG.
    `nora/infra/sql/engine.py` sets no `command_timeout`, so a Postgres that is
    slow but alive would hold every tag reply until the agent loop's own budget
    killed the whole turn as `invocation_timeout`, where it should degrade to
    bare handles in milliseconds."""
    import asyncio

    class _Hangs(FakeMediaObjectStore):
        async def lookup(self, *, sha256s):
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    metrics = Metrics()
    join = MediaJoin(
        store=_Hangs(), screen=_screen, metrics=metrics, timeout_seconds=0.05)

    got = await asyncio.wait_for(
        join.enrich([_row("睇下 [image a3f2c1]")], path="tag"), timeout=5)

    assert got[0].body == "睇下 [image a3f2c1]"
    assert metrics.get(
        "nora_media_join_total", path="tag", status="unavailable",
        reason="error") == 1


async def test_a_store_that_raises_is_counted_not_only_logged():
    """The outer `except`'s own counter. R-27's `unavailable` covers the
    `not available` branch only, so a store that broke the port's never-raise
    promise would be silent — and container logs are not on the docker log
    driver."""

    class _Explodes(FakeMediaObjectStore):
        async def lookup(self, *, sha256s):
            raise RuntimeError("db on fire")

    metrics = Metrics()
    join = MediaJoin(store=_Explodes(), screen=_screen, metrics=metrics)

    got = await join.enrich([_row("[image a3f2c1]")], path="tag")

    assert got[0].body == "[image a3f2c1]"
    assert metrics.get(
        "nora_media_join_total", path="tag", status="unavailable",
        reason="error") == 1


def test_label_agrees_refines_but_never_contradicts():
    """`label_agrees` itself, because the JOIN cannot observe every branch of
    it — and a test that ran through the join would have looked like a guard
    while proving nothing.

    Only `voice note` has a rendering consequence today (`CONTENT_LABELS`), so
    a `sticker` claimed over `audio/*` renders the same clipped detail either
    way and a join-level assertion is satisfied by both answers. The predicate
    is what has to be right: the two SEPARATE AXES the media type genuinely
    cannot express are refinements the body may make, and everything else must
    match what the record derives.

    A member's typed label reaching this function is the whole reason it
    exists, so «accepts anything» is the failure to guard against."""
    # The two refinements, each over its own top-level type.
    assert label_agrees("voice note", "audio/aac")
    assert label_agrees("sticker", "image/webp")

    # And neither over anything else.
    assert not label_agrees("voice note", "image/png")
    assert not label_agrees("voice note", "video/mp4")
    assert not label_agrees("sticker", "audio/aac")
    assert not label_agrees("sticker", "video/mp4")

    # An exact match is agreement; a contradiction is not.
    assert label_agrees("image", "image/png")
    assert not label_agrees("image", "audio/aac")

    # And a label that is not one this taxonomy produces is never accepted —
    # `LABELS` is a closed set and the body is sender-supplied.
    assert not label_agrees("", "image/png")
    assert not label_agrees("anything", "image/png")


async def test_a_sticker_label_over_audio_does_not_unclip_the_detail():
    """The join-level half: whatever `label_agrees` answers, a claimed
    `sticker` must not reach `CONTENT_LABELS`' unclipped path. Kept beside the
    predicate test because it is the CONSEQUENCE that matters and the two would
    otherwise be one test doing neither job."""
    join = _join([_obj(VOICE_SHA, media_type="audio/aac", title="a note",
                       description="about dinner", content="x" * 400)],
                 chars=20)

    got = await join.enrich([_row("[sticker b7c2d9]", sha=VOICE_SHA)])

    assert "x" * 400 not in got[0].body


async def test_an_unknown_path_label_is_refused_at_runtime():
    """CLAUDE.md's label-vocabulary rule, both halves. `JoinPath` is a
    `Literal` and the help text is derived from `get_args` of it — but five of
    the eight call sites are in packages `make gate`'s mypy does not cover, so
    a static-only guard would be a claim.

    A typo'd label would otherwise render as its own series forever and look
    like a path nobody had heard of."""
    metrics = Metrics()
    join = _join([_obj(title="a cat")], metrics=metrics)

    await join.enrich([_row("[image a3f2c1]")], path="typo")  # type: ignore[arg-type]

    assert metrics.get(
        "nora_media_join_total", path="unknown", status="ok", reason="") == 1
    assert metrics.get(
        "nora_media_join_total", path="typo", status="ok", reason="") == 0


def test_the_help_text_is_derived_from_the_vocabulary():
    """One named constant the code increments against and the help reads from.
    A list written out in `metric_specs` goes stale the first time a ninth
    builder lands, and a help text naming seven paths for an eight-path series
    is a metric that documents itself wrongly."""
    from nora.domain.media_join import JOIN_PATHS
    from nora.obs.metric_specs import _COUNTERS, _HISTOGRAMS

    by_name = {m.name: m for m in (*_COUNTERS, *_HISTOGRAMS)}
    for name in ("nora_media_join_seconds", "nora_media_join_total"):
        help_text = by_name[name].help_text
        for path in JOIN_PATHS:
            assert path in help_text, f"{name} does not document {path!r}"


async def test_legacy_slots_are_own_custody_only_and_covers_stay_suppressed():
    from dataclasses import replace

    join = _join([_obj(description="own scene"),
                  _obj(VOICE_SHA, description="foreign scene")])
    legacy = _row("original caption")
    cover = replace(legacy, media_json=legacy.media_json.replace(
        '"size": 4', '"size": 4, "role": "cover"'))
    foreign = replace(_row("[image a3f2c1]", sha=VOICE_SHA), id=2)
    got = await join.enrich([legacy, _row(""), cover, foreign])
    assert got[0].body == "original caption [image a3f2c1: own scene]"
    assert got[1].body == "[image a3f2c1: own scene]"
    assert got[2].body == "original caption"
    assert "own scene" not in got[3].body
    assert "foreign scene" in got[3].body
    assert legacy.body == "original caption"


async def test_legacy_missing_or_screened_cache_never_invents_a_reading():
    from dataclasses import replace

    rows = [_row("caption"), _row("", sha=VOICE_SHA)]
    for objects in ([], [_obj()], [_obj(description="call +85212345678")]):
        got = await _join(objects).enrich(rows)
        assert got == tuple(rows)
    # A cache hit in a different row is no authority for a body-only handle.
    forged = replace(_row("[image a3f2c1]"), media_json=None)
    got = await _join([_obj(description="own scene")]).enrich([rows[0], forged])
    assert "own scene" in got[0].body
    assert got[1].body == forged.body
