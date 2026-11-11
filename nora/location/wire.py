"""Compose this area's tool adapters from shared dependencies."""
from nora.location.adapters.tools import build as _build


def tool_specs(deps):
    return _build(deps)


def been_iso_impl(deps):
    from nora.location.adapters.tools.presence import _make__been_iso
    return _make__been_iso(deps)
