"""The Dreamer's egress screen: nothing reaches a sink carrying an identifier
or a raw coordinate.

**Dreamer output reaches the model.** `room_lexeme` renders into
`<room_language>`, `room_exchange` into `<room_exchange>`, `room_portrait` into
`<room_portrait>`, and the fact tier into `<facts>`. Nora's own identifier and
coordinate screens (R-18 in `nora.agent.egress`, R-28 in `nora.agent.security`)
sit on **her egress** — the reply she sends and the arguments she hands a third
party. An adaptor writing to its sink is a **second path into the prompt** that
passes neither of them: whatever the Dreamer persists is read back into every
later turn without ever having been screened on the way in.

With `web_fetch` in the Dreamer's tool set that matters more, not less. The
Dreamer reads attacker-influenceable pages and writes distillations of them
into a durable store. A page that says «her number is +85290000101» becomes a
lexeme sense, and the sense renders into the prompt on every subsequent turn,
long after the run that wrote it is forgotten. This is the 2026-08-20 audit's
F14 — `room_language.sense` is an unbounded model-authored persistence channel,
guarded today only by a truncation and a plausibility reject — generalised to
every tier, and settled once here rather than re-argued per adaptor.

**Where it sits, and why that is not where the plan put it.** The plan screened
the *parsed proposals* an adaptor's `accept` returns. This screens the model's
**raw envelope items** — every string reachable in every item — before any tier
type exists. Three reasons, and they are the whole placement argument:

* Screening a parsed `object` would require every adaptor to expose its own
  text for inspection — one more surface a tier can forget something on, and
  the fourth adaptor is the one that forgets. An item is a `Mapping`; it needs
  no cooperation from the tier at all.
* An item still carries the fields the typed parse **drops**. A parse that
  ignores an unknown key is exactly the parse that would hide the key an
  injection wrote into — and unknown keys are what a model under injection
  produces.
* It still drops at **item granularity**, so one poisoned item never costs its
  siblings the run. That is the property the per-batch alternative loses.

A screened-out item is dropped, counted (`nora_dream_screened_total`), and
logged — never silently discarded, and never fatal: the run's other items still
land, exactly as a malformed item is dropped rather than raised on today's
`_parse_item` path.

**`scan_identifiers`, never `scan_reply`.** `scan_reply` is `scan_identifiers`
plus the model-name pattern, and that pattern matches nine ordinary English
words — `claude`, `gemini`, `haiku`, `opus`, `sonnet` among them. It is only
ever right for text addressed to the GROUP, where the rule is «she does not SAY
it». A lexeme *about* the word 「haiku」 is precisely the sort of thing this room
coins, and screening it here would silently delete legitimate language while
looking like a security win. `nora.agent.egress`'s own docstring says the split
is deliberate and that a third name for either job is how the wrong one gets
called later; this module calls the floor predicate, by that name, and the test
suite pins the choice so it cannot be flipped by accident.

**A known boundary, stated rather than implied.** The screen reads STRINGS.
Numbers cannot hide any of the four patterns on their own — `_E164` requires a
leading `+`, `_UUID` requires hyphens, `_KEYLIKE` requires a prefix and a
separator, and `_COORD_RE` requires a comma-joined *pair* — so a scalar `float`
is not an unscreened channel. A coordinate deliberately split across two
numeric fields whose renderer later joins them would be, and the answer to that
is that no tier renders numeric fields into prose; if one ever does, the screen
is not the place that gets patched, the renderer is.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from ..obs.metrics import Metrics
from .egress import scan_identifiers
from .security import looks_like_coordinate

__all__ = ["Rejection", "screen"]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Rejection:
    """One dropped envelope item: WHICH one, WHY, and WHERE in it.

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

    Recursive on purpose. An item's values may be strings, numbers, lists of
    strings, or — because the model writes the envelope and nothing constrains
    its depth — nested mappings and lists of those. A screen that only reads
    top-level `str` values is a screen an adaptor bypasses the first time it
    accepts a structured field, and it bypasses it *silently*.

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


def _offence(item: Mapping[str, object]) -> tuple[str, str] | None:
    """The first `(reason, path)` in `item`, or None if it is clean.

    Identifiers are checked before coordinates on each string, mirroring
    `nora.agent.egress`'s ordering rule: when one string carries both, the
    counter names the exposure that would have mattered. Either way the item
    is dropped; only the label differs.
    """
    for text, path in _strings(item, ""):
        if scan_identifiers(text) is not None:
            return "identifier", path
        if looks_like_coordinate(text):
            return "coordinate", path
    return None


def screen(
    *, adaptor: str, items: Sequence[Mapping[str, object]], metrics: Metrics
) -> tuple[tuple[Mapping[str, object], ...], tuple[Rejection, ...]]:
    """Split a Dreamer envelope's items into (kept, rejected).

    Applied by the runtime to the model's raw items, before the adaptor's typed
    parse and therefore before anything can reach a sink. Returns the items
    safe to hand on and a `Rejection` per drop; raises nothing, and an empty
    batch is two empty tuples — an adaptor that found nothing is an ordinary
    outcome (R-27's `nothing_found`), not an error condition.
    """
    kept: list[Mapping[str, object]] = []
    rejected: list[Rejection] = []
    for index, item in enumerate(items):
        found = _offence(item)
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
