"""Outbound content that must never be DROPPED — the mirror of `nora.agent.egress`.

`egress` is the negative half of the outbound boundary: text that must not
leave. This is the positive half: a fact a tool already minted, that the model
then throws away.

**Why it exists (DEC-89).** `place_info` returns a `maps_url` for every place it
resolves — a real Google deep link, minted short through Nora's own shortener
(`https://n.example.net/9DpAk8g`), already screened, already in the tool result the
model is reasoning over, and named in the tool's own docstring («include it in
your reply so they can tap through»). Measured live 2026-08-10: in one
stress-test session she recommended four restaurants, `place_info` returned a
link for every one, and she used **none** of them. The data was there; the
reply was the thing that lost it.

That is DEC-82's shape exactly, one exit later. `voice.md` forbidding bold did
not stop bold, and a docstring asking for the link did not produce the link —
DEC-78's lesson is that a maxim in the prompt does not move behaviour and the
matched surface does, so the rule lives where the text LEAVES rather than being
asserted a third time in prose.

**Deliberately NARROW, because it edits her authored words** (the same bar
`strip_markdown` is held to):

* **Anchored.** A link is appended only when the reply actually NAMES the place
  it belongs to. She looked up four restaurants and recommended two: the two
  she named get their links, the two she dropped stay dropped. A tool call is
  not a promise to link.
* **Never a rescue.** Applied AFTER R-4, so no empty reply becomes a sendable
  one — the same statement `strip_markdown`'s docstring makes, and for the same
  reason: the obvious guess is the opposite.
* **Idempotent.** A url already present in the text is never appended twice, so
  a reply that DID quote the link is untouched. This is the whole no-op path for
  a model that behaves.
* **One url per place.** Two anchors (the name as asked and the name Google
  resolved) can offer the same url; it lands once.
* **Never fatal.** Decoration on a message a human is waiting for. Nothing here
  may raise into `Deliverer.deliver`.

It is not a leak guard: everything it appends was already screened by the tool
that minted it and is screened again by R-6/R-18 downstream, because this runs
BEFORE them and those guards must measure the bytes that actually leave.
"""

from __future__ import annotations

import logging

log = logging.getLogger("nora.links")

# Below this an anchor is not evidence that she named the place. Two characters
# is the floor rather than a comfortable four because Cantonese place names are
# routinely two (譚仔, 泰斗, 池記) — the length that would be reckless in English
# is the common case here, the same way `strip_markdown` had to be rewritten
# once for having reasoned about `\w` in an alphabet Nora does not write in.
_MIN_ANCHOR = 2


class LinkCarry:
    """Links minted for THIS turn, and the reply text with the dropped ones put back.

    Per turn, constructed by `nora.engagement.stages.deliver.build` and shared
    by exactly two collaborators: the tools that mint (`place_info`) and the
    `Deliverer` that sends. A process-wide instance would carry one
    conversation's places into another's reply, which is the shape of a real
    disclosure rather than a cosmetic bug — so it is built per turn like every
    other per-tag object here, and never as a module-level singleton
    (`tests/architecture/test_no_module_level_mutable_singletons.py`).
    """

    def __init__(self) -> None:
        # url -> the anchors that would justify appending it, in offer order.
        # Keyed by URL rather than by anchor because the unit of output is the
        # link: two names for one place must not append two lines.
        self._offered: dict[str, list[str]] = {}

    def offer(self, *, anchor: str, url: str) -> None:
        """Record that `url` is the link for a place the group may hear named as `anchor`.

        Called by the tool that minted the url, once per name that place is
        known by. Silently ignores anything unusable — a tool must never fail
        because its decoration did.
        """
        anchor = (anchor or "").strip()
        url = (url or "").strip()
        if not url or len(anchor) < _MIN_ANCHOR:
            return
        self._offered.setdefault(url, [])
        if anchor not in self._offered[url]:
            self._offered[url].append(anchor)

    @property
    def offered(self) -> tuple[str, ...]:
        """Every url offered this turn, in offer order. For tests and logging."""
        return tuple(self._offered)

    def apply(self, text: str) -> tuple[str, int]:
        """`text` with every earned-but-missing link appended, and how many were added.

        A link is EARNED when the reply names the place (case-folded substring
        of one of its anchors) and MISSING when the url is not already in the
        reply. Appended on their own lines, in offer order, so the shape is the
        one a person sends: the answer, then the links.

        The count is returned rather than counted internally because the
        `Metrics` instance lives with the `Deliverer`, and giving this class one
        would make it a second thing that knows how to count.
        """
        if not text or not self._offered:
            return text, 0
        haystack = text.casefold()
        additions = [
            url for url, anchors in self._offered.items()
            if url not in text and any(a.casefold() in haystack for a in anchors)
        ]
        if not additions:
            return text, 0
        log.info("reply_links_carried: %d link(s) the reply had earned and dropped",
                 len(additions))
        return text.rstrip() + "\n" + "\n".join(additions), len(additions)
