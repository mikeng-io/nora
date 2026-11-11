"""Compose this area's tool adapters from shared dependencies."""
from nora.memory.adapters.tools import build as _build
from nora.memory.adapters.tools.notes import _make__resolve


def tool_specs(deps):
    return _build(deps)


def make_resolve(deps):
    return _make__resolve(deps)
