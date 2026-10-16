"""wakegate — Nora's voice wake-gate service.

The only consumer of the voice-sidecar's TCP seam: per-user PCM in, wake
detection (openWakeWord), utterance collection, STT via the local `stt`
service, transcript forwarded to Nora's `/app/v1/voice/utterance`. Audio is
transient — forwarded, never stored.
"""
