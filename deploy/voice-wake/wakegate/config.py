"""Wake-gate settings, independent of the audio runtime."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    listen_host: str = "0.0.0.0"
    listen_port: int = 4599
    health_host: str = "127.0.0.1"
    health_port: int = 4601
    model_path: str = "/app/models/喂Nora.onnx"
    threshold: float = 0.5
    silence_tail_ms: int = 800       # no-speech tail that ends an utterance
    max_utterance_s: float = 10.0
    silence_rms: float = 400.0       # RMS fallback when vad_enabled is false
    vad_enabled: bool = True         # Silero VAD decides "finished speaking"; noise is not speech
    vad_threshold: float = 0.5       # speech probability at/above this = speech
    stt_url: str = "http://stt:8000"
    stt_model: str = "sensevoice"    # the wake path's engine; MUST equal config.example.toml [speech.wake].stt_model
    stt_language: str = "yue"
    stt_timeout_s: float = 30.0
    nora_url: str = "http://nora:9100"
    # There is no guild here. The guild arrives per CONNECTION, in the
    # sidecar's hello frame, which is what lets one gate serve every guild —
    # this service has no deployment identity to configure. Where a guild's
    # speech is RECORDED is policy.toml's business.
    #
    # The channel the words were spoken in is still not sent: only the
    # controller knows which one it is connected to, and the field is optional
    # provenance on nora's side.
    idle_timeout_s: float = 600.0

