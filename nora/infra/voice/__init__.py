"""The audio-controller seam (design/voice-channel/07-audio-owner-controller.md).

The audio-controller is the SOLE holder of the Discord voice connection;
nora drives it over HTTP and never opens a voice socket itself. This package
is the whole of nora's side of that contract: `VoiceControllerClient` (the
raw HTTP verbs), `HttpVoiceSession` (the music player's `VoiceSession` over
those verbs), and `VoiceControllerHub` (the per-guild session registry the
command and application layers talk to).
"""

from .http_controller import (
    GenerationConflict,
    HttpVoiceSession,
    VoiceControllerClient,
    VoiceControllerError,
    VoiceControllerRejected,
    VoiceControllerUnavailable,
)
from .hub import VoiceControllerHub

__all__ = [
    "GenerationConflict",
    "HttpVoiceSession",
    "VoiceControllerClient",
    "VoiceControllerError",
    "VoiceControllerHub",
    "VoiceControllerRejected",
    "VoiceControllerUnavailable",
]
