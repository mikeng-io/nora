"""Reading stored media back through a vision model (DEC-56, the READ half of
media custody; issue #116's comprehension pass on top of it). The model call
under its own timeout and byte ceiling, the comprehender that turns one call
into title/description/content, and the content-hash description cache the
`media_fetch` tool checks before either."""

from .cache import VisionCache
from .client import (
    DESCRIBE_PROMPTS,
    VISION_LOOP_SHARE,
    VisionClient,
    VisionResult,
    derive_vision_timeout,
)
from .comprehend import (
    COMPREHEND_QUESTIONS,
    MediaComprehender,
    comprehension_provenance,
    parse_comprehension,
)

__all__ = [
    "COMPREHEND_QUESTIONS",
    "DESCRIBE_PROMPTS",
    "VISION_LOOP_SHARE",
    "MediaComprehender",
    "VisionCache",
    "VisionClient",
    "VisionResult",
    "comprehension_provenance",
    "derive_vision_timeout",
    "parse_comprehension",
]
