"""Compose this area's tool adapters from shared dependencies."""
from nora.media.adapters.tools import build as _build


def tool_specs(deps):
    return _build(deps)


def media_fetch_impl(deps):
    from nora.media.adapters.tools.fetch import _make_media_fetch
    return _make_media_fetch(deps)
