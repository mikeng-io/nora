"""`MediaComprehender`'s VIDEO path — the chunked read (issue #117).

The still-image and audio halves of this adapter are pinned by the contract
suite (`tests/contract/test_media_interpreter_contract.py`) and by
`tests/test_media_fetch.py`. What lives here is the part neither of those can
reach: how a clip's chunk outputs are composed into `content`, and — the
actual bug — what happens when the chunk ceiling binds.

`VIDEO_MAX_SECONDS = 120` used to truncate every clip before the model saw it,
with no metric, no log line, and nothing in the description admitting the cut.
A ten-minute video produced a description of its first two minutes, phrased as
a description of the video: a partial read served as a complete answer. So
every test here asserts on what the read SAYS as well as on what it did.

The ffmpeg seam is `cut`, injected through `__init__` (CLAUDE.md's test-seam
policy) — `preprocess.cut_video` owns its own runner seam one level down, and
what these tests are about is the composition above it.
"""

from __future__ import annotations

from nora.infra.vision import MediaComprehender, VisionResult
from nora.infra.vision.preprocess import VideoCut


class _ChunkingVision:
    """A describer that answers per chunk. It records the BYTES it was handed,
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
    *, chunks: int, duration: float | None, covered: float | None = None,
    capped: bool = False, reason: str = "",
):
    async def cut(data, *, chunk_seconds=90, max_chunks=10) -> VideoCut:
        return VideoCut(
            chunks=tuple(f"chunk-{i}".encode() for i in range(chunks)),
            duration_seconds=duration,
            covered_seconds=(
                covered if covered is not None else float(chunks * chunk_seconds)
            ),
            capped=capped,
            reason=reason,
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
        client, cut=_cut(chunks=3, duration=270.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.content == "[0:00] segment 1\n[1:30] segment 2\n[3:00] segment 3"
    assert len(client.calls) == 3, "one describer call per chunk"
    assert client.calls == [b"chunk-0", b"chunk-1", b"chunk-2"]


async def test_a_videos_title_and_description_are_a_text_pass_over_the_chunks():
    """Not a second pass through the video. The expensive read has already
    happened once per chunk; asking the video model to watch it all again to
    write a title would double the cost of the read for a few words."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(chunks=2, duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.title == "a race"
    assert got.text.startswith("cars going round a track")
    assert client.completions == [got.content], "the text pass saw the chunk run"


async def test_a_whole_read_adds_no_caveat():
    """The positive control for the cap test below: a clip that WAS read whole
    must not carry a hedge, or the hedge stops meaning anything."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(chunks=2, duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.text == "cars going round a track"


async def test_a_capped_video_says_so_in_the_description_and_in_provenance():
    """**The actual fix.** Chunking without a REPORTED cap would just move the
    same silent lie to a larger number, so when the ceiling binds it is said
    out loud in the description AND recorded in provenance — how long the clip
    is, how much reached a model, and that the rest did not."""
    client = _ChunkingVision()
    comprehender = _comprehender(
        client, cut=_cut(chunks=3, duration=1200.0, covered=270.0, capped=True),
        max_chunks=3,
    )

    got = await comprehender.describe(data=b"video-bytes", media_type="video/mp4")

    assert "have not seen the rest" in got.text
    assert comprehender.last_provenance["video_capped"] is True
    assert comprehender.last_provenance["video_complete"] is False
    assert comprehender.last_provenance["video_duration_seconds"] == 1200.0
    assert comprehender.last_provenance["video_covered_seconds"] == 270.0


async def test_an_unmeasurable_clip_admits_it_does_not_know():
    """ffprobe could not read the duration. UNKNOWN is not COVERED — the read
    cannot claim completeness either way, and it says exactly that rather than
    inventing a number."""
    client = _ChunkingVision()
    comprehender = _comprehender(
        client, cut=_cut(chunks=1, duration=None, covered=90.0))

    got = await comprehender.describe(data=b"video-bytes", media_type="video/mp4")

    assert "may not have watched all of it" in got.text
    assert comprehender.last_provenance["video_complete"] is False
    assert comprehender.last_provenance["video_capped"] is False


async def test_a_window_that_could_not_be_read_is_a_visible_gap():
    """One refused window is not a refused read — the same call `cut_video`
    makes about ffmpeg. The gap is VISIBLE in the timecoded run rather than
    papered over, which is the whole discipline of this pair of issues."""
    client = _ChunkingVision(answers=["opening lap", "", "the finish"])

    got = await _comprehender(
        client, cut=_cut(chunks=3, duration=270.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert "[1:30] (this part could not be read)" in got.content
    assert "opening lap" in got.content and "the finish" in got.content


async def test_a_video_no_window_of_which_could_be_read_is_unavailable():
    """Every window failing is not «the video was empty» — it is «I could not
    look» (R-27). Nothing is stored, so the next sighting tries again."""
    client = _ChunkingVision(answers=["", ""])

    got = await _comprehender(
        client, cut=_cut(chunks=2, duration=180.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is False
    assert got.reason == "vision"


async def test_ffmpeg_producing_no_chunks_at_all_is_unavailable():
    """A clip nothing could be cut out of. `unavailable`, never a description
    of a video no model ever saw."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(chunks=0, duration=None, covered=0.0, reason="ffmpeg"),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is False
    assert got.reason == "ffmpeg"
    assert client.calls == []
