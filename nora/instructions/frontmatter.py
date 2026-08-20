"""Frontmatter split, YAML load, shape check, body hash — for any instruction file.

**This is the only module in `nora.instructions` that reads a `.md`.** Keep it
that way: exactly one file then needs the allowlist in
`tests/test_boundaries.py::test_doctrine_read_boundary`, and the boundary that
test protects (doctrine files have a single reader) stays true — the doctrine's
own loader is still the only thing that reads `nora/doctrine/`, it now delegates
the *skeleton* of that read to here.

What lifts and what does not (the carve line, spec §4.2): the skeleton lifts —
frontmatter split, YAML load, mapping check, required-key check, name-charset
check, body `sha256`, leftovers into `meta`. `nora.doctrine.loader.parse_section`
itself does **not**: most of it is doctrine-specific validation over
doctrine-specific required keys (`kind` against a taxonomy, `change_policy`
against a policy list, `not_here` non-empty, `authority_*` as name lists), and
`DoctrineSection` is a doctrine record. Those stay where their rules are.

`error` is a parameter rather than a fixed type because the caller's own
exception is what its callers already catch: `nora.doctrine.loader._scan`
catches `DoctrineError` to decide skip-vs-abort in lenient mode, and a startup
failure has to surface as the error that module's tests and its composition root
name. Re-wrapping in `parse_section` would change the traceback and lose the
original's message ordering; passing the class down keeps the extraction
behaviour-preserving.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Byte-identical to `nora.skills.loader`'s, which is the point: it was already
# duplicated there before this module existed, and a third copy in the Dreamer's
# loader would have been the second duplication of one regex.
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)

# The doctrine's charset, and the default here. A name is used as a dict key, a
# log field and (on the doctrine path) an XML attribute value, so it is kept to
# something that needs no escaping anywhere.
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


class InstructionError(Exception):
    """A malformed instruction document. Always names the file in its message."""


@dataclass(frozen=True)
class ParsedDocument:
    """One instruction file, split and hashed, with nothing interpreted.

    `meta` is the frontmatter with `name` already removed — every key the caller
    declared `required` is guaranteed present, and every key it did not is
    preserved rather than dropped, so a caller can decide for itself whether an
    unknown key is metadata or a typo. `sha256` is over the **body only**: the
    frontmatter carries the version, the description and the change policy, none
    of which reaches a model, so hashing it would make an editorial change to a
    `description` look like a change to what the model was told.
    """

    name: str
    body: str
    sha256: str
    meta: dict[str, Any]
    path: Path


def parse_document(
    path: Path,
    *,
    required: Sequence[str],
    error: type[Exception] = InstructionError,
    name_re: re.Pattern[str] = NAME_RE,
) -> ParsedDocument:
    """Read one `.md`, split its frontmatter, and hash its body.

    Raises `error` — with the filename first in the message, always — on a
    missing or unparseable frontmatter block, a frontmatter that is not a
    mapping, a missing required key (naming *which*), or a `name` outside
    `name_re`. Nothing here is lenient: a file that cannot be parsed has told
    the caller nothing, and guessing at its intent is the `nothing_found` /
    `unavailable` confusion one layer down.
    """
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise error(f"{path.name}: missing YAML frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise error(f"{path.name}: unparseable frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise error(f"{path.name}: frontmatter must be a mapping")
    # `name` is required whether or not the caller listed it — it is the field
    # this function returns as a first-class attribute, so a caller that omitted
    # it from `required` would get a `KeyError` from the pop below instead of the
    # filename-first message every other malformed shape gets. Hoisting it also
    # leaves a caller that DID list it first (the doctrine's `_REQUIRED`) with a
    # byte-identical key order in the message.
    required_keys = ("name", *(key for key in required if key != "name"))
    missing = [key for key in required_keys if key not in meta]
    if missing:
        keys = ", ".join(repr(k) for k in missing)
        raise error(f"{path.name}: missing required frontmatter key(s): {keys}")
    name = str(meta.pop("name"))
    if not name_re.match(name):
        raise error(f"{path.name}: name {name!r} must match {name_re.pattern}")
    body = match.group(2)
    return ParsedDocument(
        name=name,
        body=body,
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        meta=dict(meta),
        path=Path(path),
    )
