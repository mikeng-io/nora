"""Compose this area's tool adapters from shared dependencies."""
from nora.voice.adapters.tools import build as _build


def tool_specs(deps):
    return _build(deps)
