"""The parse/hash/reload core shared by every instruction corpus in this repo.

Two callers: `nora.doctrine.loader`, which assembles Nora's own system prompt on
the tag path, and `nora.instructions.dreamer`, which assembles the Dreamer's
doctrine plus one adaptor objective. What they share is a *skeleton* — split the
frontmatter, load the YAML, check the shape, hash the body, and keep serving the
last good scan when a live edit breaks — and nothing else. The doctrine's gates
(`check_kind_boundaries`, `check_normative_register`, `check_authority_graph`),
its `<section>` rendering and its `applies_to` audience filter stay in
`nora.doctrine`, because they encode doctrine-specific rules that do not
transfer (instruction-substrate spec §5).

**Why extraction rather than a second loader** (spec §4.2): `_FRONTMATTER_RE` is
already duplicated byte-identically in `nora.skills.loader`, so a third copy
would be the second duplication of one regex; and the reload state machine is
currently *unshared* — `SkillStore` has none — so a fork would be its first
second implementation, which under CLAUDE.md buys a contract suite. Its failure
mode (silently serving a stale instruction set after a bad edit) is precisely
what that machine was written to get right, and it should be got right once.

This package reads `.md` files and nothing else. It takes no config: the
directory arrives as a `Path` from the composition root.
"""

from .frontmatter import InstructionError, ParsedDocument, parse_document
from .reload import DirectoryWatcher

__all__ = [
    "DirectoryWatcher",
    "InstructionError",
    "ParsedDocument",
    "parse_document",
]
