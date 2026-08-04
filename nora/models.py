"""Moved to nora.domain.models. Kept as a re-export so this phase does not
rewrite twenty import sites; later phases migrate callers and delete this."""

from .domain.models import ConversationTarget, NormalizedMessage, PersonRef, TagContext

__all__ = ["ConversationTarget", "NormalizedMessage", "PersonRef", "TagContext"]
