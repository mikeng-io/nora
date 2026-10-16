"""Reading stored media back through a vision model (DEC-56, the READ half of
media custody, with the comprehension pass on top of it). The model call
under its own timeout and byte ceiling, the comprehender that turns one call
into title/description/content, and the content-hash description cache the
`media_fetch` tool checks before either."""

from .cache import VisionCache
from .client import (
    DESCRIBE_PROMPTS,
    VISION_LOOP_SHARE,
    VISION_WINDOW_CONCURRENCY,
    VisionClient,
    VisionResult,
    derive_vision_timeout,
    vision_waves,
)
from .comprehend import (
    COMPREHEND_QUESTIONS,
    AskWindow,
    MediaComprehender,
    comprehension_provenance,
    describe_coverage,
    parse_comprehension,
    read_video_windows,
)
from .preprocess import VideoCut, VideoWindow, cut_video

__all__ = [
    "COMPREHEND_QUESTIONS",
    "DESCRIBE_PROMPTS",
    "VISION_LOOP_SHARE",
    "VISION_WINDOW_CONCURRENCY",
    "AskWindow",
    "MediaComprehender",
    "VideoCut",
    "VideoWindow",
    "VisionCache",
    "VisionClient",
    "VisionResult",
    "comprehension_provenance",
    "cut_video",
    "derive_vision_timeout",
    "describe_coverage",
    "parse_comprehension",
    "read_video_windows",
    "vision_waves",
]
