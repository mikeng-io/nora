"""A pseudonymised case, resolved against the real deployment it runs on.

The scrub rewrote every real identifier in the tree to a pseudonym, the eval
corpora included: a case's `snapshot.conversation_id`, the
person it speaks as, the names in its input. The deployment a case runs
against is not rewritten: the eval source `nora_eval` is a clone of
production, and the subject composes with the deployment's own `policy.toml`
and `identity.toml`. So a case names a room the policy does not, and
`nora.subject.obtain._conversation_for` refuses it, as designed.

The real values cannot come back into the tree. They live in a private map on
the deploy host only, outside every checkout, named by
`NORA_EVAL_PSEUDONYM_MAP`: one `pseudonym==>real` rule per line, literal, the
reverse of a `git filter-repo --replace-text` line. A case is translated IN
MEMORY when it is loaded, before the harness reads any of it, and nothing here
ever prints a rule.

Fail-closed. With the variable unset or the file absent, nothing is translated
and a pseudonymised anchor refuses, its refusal naming this variable. A malformed map is refused,
never half-read.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

ENV = "NORA_EVAL_PSEUDONYM_MAP"
#: Appended to a refusal a pseudonymised anchor would cause.
HINT = (
    f"the corpus is pseudonymised and no map resolved it: set {ENV} to the deploy "
    "host's private pseudonym map (docs/eval-sop.md, issue #776)"
)
_ARROW = "==>"


def _path() -> Path | None:
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        # Set but absent is a typo, not "no map": refuse rather than run every
        # anchored case as if the map were off.
        raise ValueError(f"{ENV} names a file that does not exist")
    return path


@lru_cache(maxsize=4)
def _load(path: Path, mtime_ns: int) -> tuple[dict[str, str], re.Pattern[str] | None]:
    """The rules and ONE alternation over them, longest pseudonym first, so a
    pseudonym that contains a shorter one is replaced whole."""
    rules: dict[str, str] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        pseudonym, sep, real = line.partition(_ARROW)
        pseudonym, real = pseudonym.strip(), real.strip()
        if not sep or not pseudonym or not real or _ARROW in real:
            # The line number only: the line itself is private.
            raise ValueError(f"{ENV}: line {n} is not `pseudonym==>real`")
        if rules.setdefault(pseudonym, real) != real:
            raise ValueError(f"{ENV}: line {n} maps a pseudonym a second way")
    if not rules:
        return rules, None
    return rules, re.compile("|".join(
        _bounded(p) for p in sorted(rules, key=len, reverse=True)
    ))


def _bounded(token: str) -> str:
    r"""`token`, matched only where it is not part of a longer Latin word: a
    short pseudonym (a first name) must not rewrite the middle of another word.
    The boundary is ASCII only: `\w` counts a CJK character as a word
    character, and the corpora glue names to Cantonese (`阿<name>而`)."""
    left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", token[0]) else ""
    right = r"(?![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", token[-1]) else ""
    return left + re.escape(token) + right


def _active() -> tuple[dict[str, str], re.Pattern[str] | None]:
    path = _path()
    if path is None:
        return {}, None
    return _load(path, path.stat().st_mtime_ns)


def active() -> bool:
    """True when a map is in effect for this process."""
    return _active()[1] is not None


def status() -> str:
    """One line for the run header: whether the map is in effect and its size.
    Never a rule, never the path's contents."""
    rules, _ = _active()
    if rules:
        return f"pseudonym map: {len(rules)} rule(s) from {ENV}"
    return f"pseudonym map: none ({ENV} unset or missing); pseudonymised anchors refuse"


def resolve(value: Any) -> Any:
    """`value` with every pseudonym replaced by its real value: a string, or
    the strings inside a list, tuple or mapping (values only; keys are the
    corpus's own vocabulary). Anything else, and anything with no map in
    effect, is returned unchanged."""
    rules, rx = _active()
    if rx is None:
        return value
    return _walk(value, lambda s: rx.sub(lambda m: rules[m.group(0)], s))


def _walk(value: Any, sub: Any) -> Any:
    if isinstance(value, str):
        return sub(value)
    if isinstance(value, Mapping):
        return {k: _walk(v, sub) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, sub) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, sub) for v in value)
    return value
