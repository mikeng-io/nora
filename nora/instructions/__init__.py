"""The parse/hash/reload core shared by every instruction corpus in this repo.

Two callers: `nora.doctrine.loader`, which assembles Nora's own system prompt on
the tag path, and `nora.instructions.dreamer`, which assembles the Dreamer's
doctrine plus one adaptor objective. What they share is a *skeleton* — split the
frontmatter, load the YAML, check the shape, hash the body, and keep serving the
last good scan when a live edit breaks — and nothing else. The doctrine's gates
(`check_kind_boundaries`, `check_normative_register`, `check_authority_graph`),
its `<section>` rendering and its `applies_to` audience filter stay in
`nora.doctrine`, because they encode doctrine-specific rules that do not
transfer.

**One shared core, not a second loader.** `_FRONTMATTER_RE` is also duplicated
byte-identically in `nora.skills.loader`, and the reload state machine exists
only here. Its failure mode (silently serving a stale instruction set after a
bad edit) is what that machine exists to prevent, so it lives once.

This package reads `.md` files and nothing else. It takes no config: the
directory arrives as a `Path` from the composition root.
"""

from .dreamer import DreamerInstructions, InstructionSet
from .frontmatter import InstructionError, ParsedDocument, parse_document
from .reload import DirectoryWatcher, Loaded

__all__ = [
    "DirectoryWatcher",
    "DreamerInstructions",
    "InstructionError",
    "InstructionSet",
    "Loaded",
    "ParsedDocument",
    "parse_document",
]
