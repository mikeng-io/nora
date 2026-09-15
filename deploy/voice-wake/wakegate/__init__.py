"""wakegate — Nora's stage-3 voice wake-gate service (design/voice-channel/).

The only consumer of the voice-sidecar's TCP seam: per-user PCM in, wake
detection (openWakeWord), utterance collection, STT via the local `stt`
service, transcript forwarded to Nora's `/app/v1/voice/utterance`. Audio is
transient — forwarded, never stored.
"""
