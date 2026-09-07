"""`MediaComprehender`'s VIDEO path — the chunked read (issue #117).

The still-image and audio halves of this adapter are pinned by the contract
suite (`tests/contract/test_media_interpreter_contract.py`) and by
`tests/test_media_fetch.py`. What lives here is the part neither of those can
reach: how a clip's window outputs are composed into `content`, what the answer
SAYS about the parts it did not see, and where the timecodes come from.

`VIDEO_MAX_SECONDS = 120` used to truncate every clip before the model saw it,
with no metric, no log line, and nothing in the description admitting the cut.
A ten-minute video produced a description of its first two minutes, phrased as
a description of the video: a partial read served as a complete answer. So
every test here asserts on what the read SAYS as well as on what it did — and
on all THREE ways a read comes back short, because the first cut of this only
noticed one of them (the chunk ceiling) and served the other two silently.

The ffmpeg seam is `cut`, injected through `__init__` (CLAUDE.md's test-seam
policy) — `preprocess.cut_video` owns its own runner seam one level down, and
what these tests are about is the composition above it.
"""

from __future__ import annotations

from nora.infra.vision import MediaComprehender, VisionResult
from nora.infra.vision.preprocess import VideoCut, VideoWindow


class _ChunkingVision:
    """A describer that answers per window. It records the BYTES it was handed,
    which is what proves each window really was a separate call rather than one
    view described once."""

    def __init__(self, *, answers: list[str] | None = None) -> None:
        self.answers = answers
        self.calls: list[bytes] = []
        self.completions: list[str] = []

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = "",
        provider: str = "omniroute", max_bytes: int | None = None,
    ) -> VisionResult:
        self.calls.append(data)
        if self.answers is None:
            return VisionResult(
                available=True, description=f"segment {len(self.calls)}")
        index = len(self.calls) - 1
        answer = self.answers[index] if index < len(self.answers) else ""
        if not answer:
            return VisionResult(available=False, reason="timeout")
        return VisionResult(available=True, description=answer)

    async def complete(self, *, prompt: str, text: str, model: str = "") -> str:
        self.completions.append(text)
        return '{"title": "a race", "description": "cars going round a track"}'


def _cut(
    *, starts: list[float], duration: float | None, capped: bool = False,
    seconds: float = 90.0, reason: str = "",
):
    """A cut whose windows carry the start times a test wants — INCLUDING a run
    with a hole in it, which is the case the timecode bug lived in."""
    async def cut(data, *, chunk_seconds=90, max_chunks=10) -> VideoCut:
        windows = tuple(
            VideoWindow(
                start_seconds=start, seconds=seconds,
                data=f"chunk@{start:.0f}".encode(),
            )
            for start in starts
        )
        return VideoCut(
            windows=windows, duration_seconds=duration,
            covered_seconds=len(windows) * seconds, capped=capped, reason=reason,
        )
    return cut


def _comprehender(client, *, cut, max_chunks: int = 10) -> MediaComprehender:
    return MediaComprehender(
        client=client, describe_models={"video": "k3"},
        describe_providers={"video": "kimi"}, text_model="k3",
        chunk_seconds=90, max_chunks=max_chunks, cut=cut,
    )


async def test_a_videos_content_is_its_chunk_outputs_in_order_timecoded():
    """Issue #117's shape: the clip is cut into transient windows at READ time
    (storage is untouched — one blob, the original), each window goes to the
    EXISTING describer path unchanged, and `content` is what came back, in
    order, with a timecode so a reader can say WHEN something happened."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0, 180.0], duration=270.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.content == "[0:00] segment 1\n[1:30] segment 2\n[3:00] segment 3"
    assert len(client.calls) == 3, "one describer call per window"
    assert client.calls == [b"chunk@0", b"chunk@90", b"chunk@180"]


async def test_a_dropped_window_does_not_shift_its_neighbours_timecodes():
    """**The timecode comes from the WINDOW, never from its position in the
    list.** `cut_video` drops a window ffmpeg refused rather than leaving a
    hole, so `windows[k]` is not the k-th window of the clip once anything has
    failed — a 6-minute clip losing its 1:30 window rendered the 3:00 content
    as `[1:30]`, and Nora would then say «at 1:30 he does X» about something at
    3:00. Every number below is a real instant in the source."""
    client = _ChunkingVision()

    got = await _comprehender(
        # ffmpeg produced 0:00, 3:00 and 4:30 — the 1:30 window is simply not
        # there, exactly as `cut_video`'s `continue` leaves it.
        client, cut=_cut(starts=[0.0, 180.0, 270.0], duration=360.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.content == (
        "[0:00] segment 1\n[3:00] segment 2\n[4:30] segment 3"
    )
    assert "[1:30]" not in got.content


async def test_a_videos_title_and_description_are_a_text_pass_over_the_chunks():
    """Not a second pass through the video. The expensive read has already
    happened once per window; asking the video model to watch it all again to
    write a title would double the cost of the read for a few words."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0], duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.title == "a race"
    assert got.text.startswith("cars going round a track")
    assert client.completions == [got.content], "the text pass saw the chunk run"


async def test_a_whole_read_adds_no_caveat():
    """The positive control for every caveat below: a clip that WAS read whole
    must not carry a hedge, or the hedge stops meaning anything."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0], duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.text == "cars going round a track"
    assert got.provenance is not None
    assert got.provenance["video_complete"] is True


async def test_a_capped_video_says_so_in_the_description_and_in_provenance():
    """Chunking without a REPORTED cap would just move the same silent lie to a
    larger number, so when the ceiling binds it is said out loud in the
    description AND recorded in provenance — how long the clip is, how much
    reached a model, and that the rest did not."""
    client = _ChunkingVision()

    got = await _comprehender(
        client,
        cut=_cut(starts=[0.0, 90.0, 180.0], duration=1200.0, capped=True),
        max_chunks=3,
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert "have not seen the rest" in got.text
    assert got.provenance is not None
    assert got.provenance["video_capped"] is True
    assert got.provenance["video_complete"] is False
    assert got.provenance["video_duration_seconds"] == 1200.0


async def test_a_read_shortened_by_a_refused_window_says_so_too():
    """**The ceiling is not the only way a read comes back short**, and the
    first cut of this only noticed the ceiling: `note()` returned `""` whenever
    `capped` was False, so a clip whose middle would not open was served with a
    confident description and no caveat. `complete` was correctly False and
    nothing acted on it. DEC-161 is «never truncate silently», not «never
    truncate»."""
    client = _ChunkingVision()

    got = await _comprehender(
        # 6 minutes of clip, two windows cut: ffmpeg refused the rest.
        client, cut=_cut(starts=[0.0, 90.0], duration=360.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert "have not seen" in got.text
    assert got.provenance is not None
    assert got.provenance["video_capped"] is False, "the ceiling did not bind"
    assert got.provenance["video_complete"] is False


async def test_a_model_refusal_shortens_what_the_read_may_claim():
    """The THIRD way a read comes back short, and the one the cut cannot see:
    ffmpeg produced every window cleanly and the MODEL refused most of them.
    Coverage is measured against the windows a model actually described, or
    `provenance` would record «whole clip covered» beside a content full of gap
    markers — and an operator reading it to decide whether to re-read gets a
    false negative."""
    client = _ChunkingVision(answers=["opening lap", "", ""])

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0, 180.0], duration=270.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert "could not watch" in got.text
    assert got.provenance is not None
    assert got.provenance["video_windows_cut"] == 3
    assert got.provenance["video_windows_read"] == 1
    assert got.provenance["video_covered_seconds"] == 90.0
    assert got.provenance["video_complete"] is False


async def test_a_real_description_containing_the_gap_words_is_not_miscounted():
    """The gap marker is recognised STRUCTURALLY — by which windows answered —
    never by searching the rendered text for a phrase. A clip of somebody
    saying «the sign could not be read» would otherwise be counted as a
    failure, and a fully-read clip reported as partial."""
    client = _ChunkingVision(
        answers=["he says this part could not be read", "the finish"])

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0], duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.provenance is not None
    assert got.provenance["video_windows_read"] == 2
    assert got.provenance["video_complete"] is True
    assert got.text == "cars going round a track", "no caveat on a whole read"


async def test_an_unmeasurable_clip_admits_it_does_not_know():
    """ffprobe could not read the duration. UNKNOWN is not COVERED — the read
    cannot claim completeness either way, and it says exactly that rather than
    inventing a number."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[0.0], duration=None),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert "may not have watched all of it" in got.text
    assert got.provenance is not None
    assert got.provenance["video_complete"] is False
    assert got.provenance["video_capped"] is False


async def test_a_window_that_could_not_be_read_is_a_visible_gap():
    """One refused window is not a refused read — the same call `cut_video`
    makes about ffmpeg. The gap is VISIBLE in the timecoded run rather than
    papered over, which is the whole discipline of this pair of issues."""
    client = _ChunkingVision(answers=["opening lap", "", "the finish"])

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0, 180.0], duration=270.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert "[1:30] (this part could not be read)" in got.content
    assert "opening lap" in got.content and "the finish" in got.content


async def test_a_video_no_window_of_which_could_be_read_is_unavailable():
    """Every window failing is not «the video was empty» — it is «I could not
    look» (R-27). Nothing is stored, so the next sighting tries again."""
    client = _ChunkingVision(answers=["", ""])

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0], duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is False
    assert got.reason == "vision"


async def test_ffmpeg_producing_no_windows_at_all_is_unavailable():
    """A clip nothing could be cut out of. `unavailable`, never a description
    of a video no model ever saw. (`media_fetch` degrades differently, to a
    single view — the deliberate look has a human waiting on it; the automatic
    pass would rather retry on the next sighting than store a guess.)"""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[], duration=None, reason="ffmpeg"),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is False
    assert got.reason == "ffmpeg"
    assert client.calls == []


async def test_a_video_with_no_text_model_is_a_wiring_gap_not_a_read():
    """**The shipped default's own path.** `comprehension_text_model = ""`
    makes the synthesis answer `("", "")`, and returning `available=True` there
    wrote a row with an empty title and an empty description, counted it `ok`,
    set `comprehended`, made the transcript render a bare `[video …]` forever
    and every future sighting short-circuit to `already_read` — a counter
    saying the describer works over a capability that silently does not.
    `_comprehend_still` had refused this since it was written."""
    client = _ChunkingVision()
    comprehender = MediaComprehender(
        client=client, describe_models={"video": "k3"},
        text_model="", chunk_seconds=90, max_chunks=10,
        cut=_cut(starts=[0.0, 90.0], duration=180.0),
    )

    got = await comprehender.describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is False
    assert got.reason == "no_text_describer"
    assert (got.title, got.text) == ("", "")


async def test_a_capped_video_still_answers_when_the_text_model_is_absent():
    """The positive control for the guard above, and the case that keeps it
    from being too strict: with no text model but a COVERAGE note to carry, the
    description is non-empty and the read is real. Refusing it would throw away
    a timecoded run of a clip somebody sent over a missing headline."""
    client = _ChunkingVision()
    comprehender = MediaComprehender(
        client=client, describe_models={"video": "k3"},
        text_model="", chunk_seconds=90, max_chunks=2,
        cut=_cut(starts=[0.0, 90.0], duration=1200.0, capped=True),
    )

    got = await comprehender.describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.title == ""
    assert "have not seen the rest" in got.text
    assert got.content.startswith("[0:00]")


async def test_two_concurrent_reads_do_not_share_provenance():
    """`last_provenance` used to be stashed on the adapter and read after an
    await. The comprehension job spawns ONE TASK PER SUBJECT, so a message
    carrying a video and a photo runs two `describe` calls concurrently on one
    shared instance — the still's reset landed mid chunk-loop and the video's
    chunk record, the RECORDED half of DEC-161, was lost or attached to the
    wrong row. Single-flight is per-sha256, not per-instance, so it never
    established the safety the old comment claimed. A value that travels with
    its result cannot race."""
    import asyncio

    client = _ChunkingVision()
    comprehender = MediaComprehender(
        client=client, describe_models={"video": "k3", "image": "glm"},
        text_model="k3", chunk_seconds=90, max_chunks=10,
        cut=_cut(starts=[0.0, 90.0], duration=1200.0, capped=True),
    )

    video, still = await asyncio.gather(
        comprehender.describe(data=b"video-bytes", media_type="video/mp4"),
        comprehender.describe(data=b"\x89PNG", media_type="image/png"),
    )

    assert video.provenance is not None
    assert video.provenance["video_capped"] is True
    assert still.provenance is None, "a still leaves no video record behind"
