"""Screen model-authored values before they enter a persisted result or tool write.

Debrief screens each item independently. Lessons and session write verbs use
its predicate directly. Nested text and map keys are screened because either
can reappear in a later prompt. Content checks are configurable. Credential
protection and the tool's scope and admission checks apply independently.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from ..obs.metrics import Metrics
from .egress import _E164, _canonical, scan_identifiers
from .security import looks_like_coordinate

__all__ = ["Rejection", "offence", "screen"]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Rejection:
    """The index, reason and field path of one dropped item.

    `index` is the item's position in the batch the model returned, `reason` is
    the metric label (`"identifier"` or `"coordinate"`), and `path` names the
    field the offending string was reached through (`"sense"`,
    `"aliases[2]"`, `"evidence.quote"`).

    Deliberately NOT the offending text. A log line that reproduces it copies
    somebody's phone number out of a store that refused to hold it and into a
    log file that will hold it forever — the exposure this module exists to
    prevent, one file over. The three fields are enough to find the item in the
    run's Langfuse trace, which is where the content legitimately lives.
    """

    index: int
    reason: str
    path: str


def _strings(value: object, path: str) -> Iterator[tuple[str, str]]:
    """Every string reachable from `value`, with the path it was reached by.

    The model can write nested mappings and lists, so screening recurses into
    structured fields.

    Mapping KEYS are walked too. A field name never trips any of the four
    patterns, so this costs nothing on well-formed output; it closes the case
    where a model under injection writes content into the key half of a pair.
    """
    if isinstance(value, str):
        yield value, path
        return
    if isinstance(value, Mapping):
        for key, sub in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if isinstance(key, str):
                yield key, f"{key_path}<key>"
            yield from _strings(sub, key_path)
        return
    # `str` and `bytes` are Sequences; both are handled above or are not text
    # this screen can read. Everything else iterable (list, tuple, set as a
    # Sequence-alike) is walked element-wise.
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for i, sub in enumerate(value):
            yield from _strings(sub, f"{path}[{i}]")


#: What THIS screen refuses beyond the reply floor. `scan_identifiers` is the
#: reply's R-18 floor and stays exactly what it is; a SINK injected into every
#: later prompt has a stricter job: the standing fold sees tool results at up
#: to 4000 characters and keeps what it finds. Refused here: an email, an
#: 18-digit platform id, a `where_is` result's `{'lat': …, 'lng': …}` and
#: «lat 22.3 lng 114.1». Each maps onto an EXISTING reason label, so the
#: metric's vocabulary does not move. Street addresses are not here: no pattern
#: for them is reliable in two languages, and a guess is a screen that drops
#: real prose.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_ID = re.compile(r"(?<![\d.])\d{15,}(?![\d.])")
_LABELLED_COORD = re.compile(
    r"\b(?:lat|latitude)\b['\"]?\s*[:=]?\s*-?\d{1,2}\.\d{3,}"
    r".{0,40}?\b(?:lng|lon|long|longitude)\b['\"]?\s*[:=]?\s*-?\d{1,3}\.\d{3,}",
    re.IGNORECASE | re.DOTALL,
)


def _sink_identifier(text: str, *, content_checks: bool = True) -> bool:
    identifiers = text if content_checks else _E164.sub("", _canonical(text))
    return bool(_EMAIL.search(text) or _LONG_ID.search(identifiers))


def _offence(item: Mapping[str, object], *, content_checks: bool = True) -> tuple[str, str] | None:
    """The first `(reason, path)` in `item`, or None if it is clean.

    Identifiers are checked before coordinates on each string, mirroring
    `nora.agent.egress`'s ordering rule: when one string carries both, the
    counter names the exposure that would have mattered. Either way the item
    is dropped; only the label differs.
    """
    for text, path in _strings(item, ""):
        if scan_identifiers(text, content_checks=content_checks) is not None or _sink_identifier(
            text, content_checks=content_checks
        ):
            return "identifier", path
        if content_checks and (looks_like_coordinate(text) or _LABELLED_COORD.search(text)):
            return "coordinate", path
    return None


def offence(item: Mapping[str, object], *, content_checks: bool = True) -> tuple[str, str] | None:
    """The first offence in the values passed to a persisted write."""
    return _offence(item, content_checks=content_checks)


def text_offence(text: str, *, content_checks: bool = True) -> str | None:
    """`offence` on one text: `"identifier"`, `"coordinate"` or `None`. The
    Profile write's screen (`nora.infra.sql.profile.SqlProfileStore`)."""
    found = _offence({"text": text}, content_checks=content_checks)
    return None if found is None else found[0]


def screen(
    *, adaptor: str, items: Sequence[Mapping[str, object]], metrics: Metrics,
    content_checks: bool = True,
) -> tuple[tuple[Mapping[str, object], ...], tuple[Rejection, ...]]:
    """Return the clean items and one `Rejection` for each dropped item."""
    kept: list[Mapping[str, object]] = []
    rejected: list[Rejection] = []
    for index, item in enumerate(items):
        found = _offence(item, content_checks=content_checks)
        if found is None:
            kept.append(item)
            continue
        reason, path = found
        rejected.append(Rejection(index=index, reason=reason, path=path))
        metrics.inc("nora_dream_screened_total", adaptor=adaptor, reason=reason)
        log.warning(
            "dream_screened: adaptor=%s item=%d reason=%s path=%s",
            adaptor, index, reason, path,
        )
    return tuple(kept), tuple(rejected)
