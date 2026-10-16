"""`MediaComprehender`'s VIDEO path — the chunked read.

The still-image and audio halves of this adapter are pinned by the contract
suite (`tests/contract/test_media_interpreter_contract.py`) and by
`tests/test_media_fetch.py`. What lives here is the part neither of those can
reach: how a clip's window outputs are composed into `content`, what the answer
SAYS about the parts it did not see, and where the timecodes come from.

A partial read must never be served as a complete answer, such as a description of a clip's first
minutes phrased as a description of the video. So every test here asserts on what the read SAYS as
well as on what it did — and on all THREE ways a read comes back short (the chunk ceiling, windows
the cut dropped, windows the model refused).

The ffmpeg seam is `cut`, injected through `__init__` (CLAUDE.md's test-seam
policy) — `preprocess.cut_video` owns its own runner seam one level down, and
what these tests are about is the composition above it.
"""

from __future__ import annotations

from nora.domain.blocks import wrap_outside_text
from nora.infra.vision import MediaComprehender, VisionResult
from nora.infra.vision.comprehend import _SUMMARIZE_SOURCE
from nora.infra.vision.preprocess import VideoCut, VideoWindow


class _ChunkingVision:
    """A describer that answers per window. It records the BYTES it was handed,
    which is what proves each window really was a separate call rather than one
    view described once."""

    def __init__(self, *, answers: list[str] | None = None) -> None:
        self.answers = answers
        self.calls: list[bytes] = []
        #: The `windows` argument each call was handed. The per-call vision
        #: ceiling divides by `ceil(windows / concurrency)`, so a double that
        #: swallowed the argument would let a regression that stopped passing it
        #: pass silently — this is the same reasoning as `calls` above, which
        #: records the bytes to prove each window was its own call.
        self.windows: list[int | None] = []
        self.completions: list[str] = []
        #: (prompt, provider) per call — what the MODEL was actually asked, and
        #: over which transport, after every call-site re-templating.
        self.asked: list[tuple[str, str]] = []

    async def describe(
        self, *, data: bytes, media_type: str, prompt: str = "", model: str = "",
        provider: str = "omniroute", max_bytes: int | None = None,
        windows: int | None = None,
    ) -> VisionResult:
        self.calls.append(data)
        self.windows.append(windows)
        self.asked.append((prompt, provider))
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
    """The clip is cut into transient windows at READ time
    (storage is untouched — one blob, the original), each window goes to the
    EXISTING describer path unchanged, and `content` is what came back, in
    order, with a timecode so a reader can say WHEN something happened."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[0.0, 90.0, 180.0], duration=270.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.content == "0:00 — segment 1\n1:30 — segment 2\n3:00 — segment 3"
    assert len(client.calls) == 3, "one describer call per window"
    assert client.calls == [b"chunk@0", b"chunk@90", b"chunk@180"]


async def test_a_dropped_window_does_not_shift_its_neighbours_timecodes():
    """**The timecode comes from the WINDOW, never from its position in the
    list.** `cut_video` drops a window ffmpeg refused rather than leaving a
    hole, so `windows[k]` is not the k-th window of the clip once anything has
    failed — a 6-minute clip losing its 1:30 window would render the 3:00 content
    as `[1:30]`, and Nora would then say «at 1:30 he does X» about something at
    3:00. Every number below is a real instant in the source."""
    client = _ChunkingVision()

    got = await _comprehender(
        # ffmpeg produced 0:00, 3:00 and 4:30 — the 1:30 window is simply not
        # there, exactly as `cut_video`'s `continue` leaves it.
        client, cut=_cut(starts=[0.0, 180.0, 270.0], duration=360.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.content == (
        "0:00 — segment 1\n3:00 — segment 2\n4:30 — segment 3"
    )
    assert "1:30 —" not in got.content


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
    # In its own `media_text` block: the
    # chunk run is a model's notes on somebody's video, framed as data.
    assert client.completions == [
        wrap_outside_text("media_text", _SUMMARIZE_SOURCE, got.content),
    ], "the text pass saw the chunk run"


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
    assert "media_caveat" not in got.provenance
    # EVERY window call carries the read's TOTAL window count — the serial
    # factor the per-call ceiling divides by. Two windows here, so each of the
    # two calls must say 2: not 1 (which would hand each window a ceiling
    # derived for a single-view read and blow the loop on a long clip), and not
    # the running index (which is a different quantity entirely).
    assert client.windows == [2, 2], (
        f"the window count did not reach the describer: {client.windows}"
    )


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

    assert "have not seen the rest" in got.provenance["media_caveat"]
    assert got.provenance is not None
    assert got.provenance["video_capped"] is True
    assert got.provenance["video_complete"] is False
    assert got.provenance["video_duration_seconds"] == 1200.0
    # NOT inside the description: appended there, the transcript's `comprehension_render_chars`
    # bound would clip it off — worst of all when the description is rich, which is the case a
    # confident-sounding partial read does the most damage in. Kept apart, every renderer appends
    # it last and after its own clip.
    assert "have not seen" not in got.text


async def test_a_read_shortened_by_a_refused_window_says_so_too():
    """**The ceiling is not the only way a read comes back short**: a clip whose middle would not
    open carries a caveat even though `capped` is False (`complete` is False). DEC-161 is «never
    truncate silently», not «never
    truncate»."""
    client = _ChunkingVision()

    got = await _comprehender(
        # 6 minutes of clip, two windows cut: ffmpeg refused the rest.
        client, cut=_cut(starts=[0.0, 90.0], duration=360.0),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.provenance is not None
    assert "have not seen" in got.provenance["media_caveat"]
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
    assert got.provenance is not None
    assert "could not watch" in got.provenance["media_caveat"]
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
    assert "media_caveat" not in got.provenance, "no caveat on a whole read"


async def test_an_unmeasurable_clip_admits_it_does_not_know():
    """ffprobe could not read the duration. UNKNOWN is not COVERED — the read
    cannot claim completeness either way, and it says exactly that rather than
    inventing a number."""
    client = _ChunkingVision()

    got = await _comprehender(
        client, cut=_cut(starts=[0.0], duration=None),
    ).describe(data=b"video-bytes", media_type="video/mp4")

    assert got.provenance is not None
    assert "may not have watched all of it" in got.provenance["media_caveat"]
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
    assert "1:30 — this part could not be read" in got.content
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


async def test_a_video_with_no_text_model_keeps_its_window_run():
    """**The shipped default's own path, and it must not throw the read away.**
    `comprehension_text_model = ""` makes the synthesis answer `("", "")` — but
    the expensive half has already happened: `content` is a timecoded run of up
    to ten video-model calls, and `[media] comprehension_text_model` documents
    exactly that as what survives without a text pass.

    Refusing would discard all ten calls and make the next sighting pay again; a titleless row is
    safe because `media_detail` falls back to `content` when there is no headline
    (`tests/unit/test_engagement_context.py`)."""
    client = _ChunkingVision()
    comprehender = MediaComprehender(
        client=client, describe_models={"video": "k3"},
        text_model="", chunk_seconds=90, max_chunks=10,
        cut=_cut(starts=[0.0, 90.0], duration=180.0),
    )

    got = await comprehender.describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert (got.title, got.text) == ("", "")
    assert got.content == "0:00 — segment 1\n1:30 — segment 2"
    assert len(client.calls) == 2, "and the ten calls were not wasted"


async def test_a_capped_video_with_no_text_model_keeps_both_run_and_caveat():
    """The two halves that survive a missing text model, together: the window
    run as `content`, and what the read did not see as its own caveat — which is a separate value
    precisely so it cannot be lost to an empty
    description or to a clip."""
    client = _ChunkingVision()
    comprehender = MediaComprehender(
        client=client, describe_models={"video": "k3"},
        text_model="", chunk_seconds=90, max_chunks=2,
        cut=_cut(starts=[0.0, 90.0], duration=1200.0, capped=True),
    )

    got = await comprehender.describe(data=b"video-bytes", media_type="video/mp4")

    assert got.available is True
    assert got.title == ""
    assert got.content.startswith("0:00 —")
    assert got.provenance is not None
    assert "have not seen the rest" in got.provenance["media_caveat"]


async def test_two_concurrent_reads_do_not_share_provenance():
    """Provenance travels with the result, not on the adapter. The comprehension job spawns ONE TASK
    PER SUBJECT, so a message carrying a video and a photo runs two `describe` calls concurrently on
    one shared instance; state kept on the instance would let the still's reset land mid chunk-loop
    and lose or misattach the video's chunk record, the RECORDED half of DEC-161. Single-flight is
    per-sha256, not per-instance. A value that travels with its result cannot race."""
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


async def test_the_automatic_comprehension_question_asks_for_speech():
    """DEC-197: the AUTOMATIC read — the one an Instagram Reel or Story goes
    through, not `media_fetch` — must ask for what is SAID, now that the window
    carries its audio track. Asserted on the prompt the describer was actually
    handed, after `_comprehend_video` re-templates it at the call site, and on
    the transport it travelled: the configured `opencode-go` lane.

    The identity-number request rides with it (the operator's line: phone, ID
    and account numbers). It is a request to a
    model, not a screen; the DEC row records the exposure that remains."""
    client = _ChunkingVision()
    comprehender = MediaComprehender(
        client=client,
        describe_models={"video": "mimo-v2.6-flash", "audio": "mimo-v2.6-flash"},
        describe_providers={"video": "opencode-go", "audio": "opencode-go"},
        text_model="", chunk_seconds=90, max_chunks=10,
        cut=_cut(starts=[0.0], duration=30.0),
    )

    await comprehender.describe(data=b"video-bytes", media_type="video/mp4")
    await comprehender.describe(data=b"audio-bytes", media_type="audio/mp4")

    (video_prompt, video_provider), (audio_prompt, audio_provider) = client.asked
    for prompt in (video_prompt, audio_prompt):
        assert "transcribe what" in prompt, prompt
        assert "phone number, ID number or account number" in prompt, prompt
    assert "text on screen" in video_prompt
    assert (video_provider, audio_provider) == ("opencode-go", "opencode-go")
