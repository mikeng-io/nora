"""Compose this area's tool adapters from shared dependencies."""
from nora.documents.adapters.tools import build as _build
from nora.documents.adapters.tools import session_tool_specs as _session_tool_specs


def tool_specs(deps):
    return _build(deps)


def session_tool_specs(service, reader, authority):
    """A session's document tools, bound to its own capability (never a person)."""
    return _session_tool_specs(service, reader, authority)
