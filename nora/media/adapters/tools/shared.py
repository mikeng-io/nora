from __future__ import annotations

from nora.domain.video import ReferenceKind

_VIDEO_REF_KINDS: dict[str, ReferenceKind] = {"image": "image", "video": "video", "audio": "audio"}

_VIDEO_REF_WORDS: dict[str, str] = {
    "image": "photos",
    "video": "clips",
    "audio": "voice notes or other sound",
}
