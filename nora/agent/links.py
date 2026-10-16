"""Outbound content that must never be DROPPED — the mirror of `nora.agent.egress`.

`egress` is the negative half of the outbound boundary: text that must not
leave. This is the positive half: a fact a tool already minted, that the model
then throws away.

**Why it exists (DEC-89).** `place_info` returns a `maps_url` for every place it
resolves — a real Google deep link (the full Maps URL, ADR 0023: no
shortener), already screened, already in the tool result the
model is reasoning over, and named in the tool's own docstring («include it in
your reply so they can tap through»). The model can still drop it from the
reply, and a maxim in the prompt does not reliably move behaviour (DEC-78,
DEC-82), so the rule lives where the text LEAVES rather than being asserted
again in prose.

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

**A link to one of OUR documents is minted, never authored (R-100).**
Only `document_publish`, `document_read` and `document_set_status` return a
document URL, and the `<documents>` block she reads carries a title and an id,
never a URL. So she has nothing to write and only something to copy. A document
link the carrier was not handed is REMOVED, and the link of a document published
this turn is added, which makes the claim true or absent. The link in the room
is the receipt; if it is not there, nothing was saved.

**A turn that reached for `document_publish` and saved nothing says so.** She
may tell the room a document was made without ever publishing it, with no link
in the reply for anything to strip. The one fact that is true in every such
turn, whatever she wrote, is that nothing was saved, so the reply carries it
once, whether or not a public origin is configured.

It goes on a message that can be the last one, never on a progress message sent before the
work: a catalog read alone does not mark a message sent through `send_reply` (she may still be
writing), while a publish that was tried and refused does, and so does any message the turn
ends on (recovered, salvaged, drafted or the fallback). A turn that ends through `send_reply`
after reading the catalog and nothing else is not caught.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote_plus

from nora.domain.media import MediaBlob
from nora.domain.ports import OutboundAttachment

log = logging.getLogger("nora.links")

# Below this an anchor is not evidence that she named the place. Two characters
# is the floor rather than a comfortable four because Cantonese place names are
# routinely two (譚仔, 泰斗, 池記) — the length that would be reckless in English
# is the common case here (and `\w` reasoning does not fit the scripts Nora
# writes in, as `strip_markdown` also accounts for).
_MIN_ANCHOR = 2

# Appended to a reply when `document_publish` was called this turn and none stored a document.
UNSAVED_DOCUMENT_NOTE = "(No document was saved.)"


def own_document_link_pattern(public_url: str) -> re.Pattern[str]:
    """`<public_url>/[locale/]documents/<hex run>`, scheme optional, any case: the shape a link
    to one of our documents is built in (the portal serves it under a locale prefix too).
    Group 1 is the whole hex run, so a run that is not exactly the handle is a different handle,
    never a prefix of a real one. The boundaries are ASCII on purpose: Python's `\\w` and `\\b`
    count a Chinese character as a word character, so a link written with no space beside
    Cantonese would not match."""
    host = public_url.split("://", 1)[-1].strip("/")
    return re.compile(
        rf"(?<![A-Za-z0-9.-])(?:https?://)?(?:www\.)?{re.escape(host)}(?::\d+)?/"
        rf"(?:[a-z]{{2}}(?:-[A-Za-z0-9]{{2,8}})*/)?documents/([0-9a-f]{{6,}})(?![0-9A-Za-z])",
        re.IGNORECASE,
    )


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
        # Our own documents (R-100). `None` until a documents tool arms it with the public origin,
        # which is also the only time stripping is possible: no origin, no document links exist.
        self._doc_pattern: re.Pattern[str] | None = None
        self._doc_handles: set[str] = set()            # handed to her this turn, may be repeated
        self._doc_receipts: dict[str, str] = {}        # handle -> url, published this turn
        self._stripped = 0
        self._documents_added = 0
        self._document_called = False                  # `document_publish` was called this turn
        self._document_tried = False                   # ... with a spec, so a save was attempted
        self._document_saved = False                   # ... and one call stored a document
        self._unsaved_noted = False                    # a sent message carries the note
        # Links code hands the room with this turn's message (a finished job's result URL):
        # appended when missing, until a sent message carries them.
        self._attached: list[str] = []
        # Media code hands the room with this turn's message (a finished video clip's bytes),
        # with the custody handles its audit row records: carried until a message holding them
        # is sent.
        self._media: list[OutboundAttachment] = []
        self._media_blobs: list[MediaBlob] = []

    def claim_documents(self, public_url: str) -> None:
        """Arm the document rule for this turn with the origin documents are linked under.
        A tool calls this when it is built, so the rule holds from the first reply, including
        a reply written after a publish that never returned `ok`."""
        if public_url and self._doc_pattern is None:
            self._doc_pattern = own_document_link_pattern(public_url)

    def document_called(self) -> None:
        """`document_publish` was called this turn, with or without a spec."""
        self._document_called = True

    def document_tried(self) -> None:
        """`document_publish` was called with a spec this turn, whatever it returned."""
        self._document_called = self._document_tried = True

    def document_saved(self) -> None:
        """A `document_publish` call stored a document this turn."""
        self._document_saved = True

    def mint_document(self, url: str, *, published: bool) -> None:
        """Record a document URL a tool returned. `published` means it was saved THIS turn, so
        the link is added to the reply; a read or a restore only lets her repeat it."""
        pattern = self._doc_pattern
        match = pattern.search(url or "") if pattern is not None else None
        if match is None:
            return
        handle = match.group(1).lower()
        self._doc_handles.add(handle)
        if published:
            self._doc_receipts[handle] = url.strip()

    def attach(self, url: str) -> None:
        """A link the room must receive with this turn's message, carried by code rather than
        copied by the model: a finished video's signed URL is long and one wrong character
        breaks its signature. Appended to a message that lacks it, until one that carries it
        is sent. Not a document link, so the document rule never strips it."""
        url = (url or "").strip()
        if url and url not in self._attached:
            self._attached.append(url)

    def carry_media(
        self, attachment: OutboundAttachment, blobs: tuple[MediaBlob, ...] = (),
    ) -> None:
        """Media the room must receive with this turn's message, carried by code: a finished
        video clip, which the model is told is attached and cannot attach itself. The
        `Deliverer` appends it to the attachments of every message that passes R-4 (a non-empty
        reply, or one with attachments of its own) until one is sent: a message a later guard
        refuses, or the platform rejects, leaves it carried for the next. Nothing here falls
        back when a platform rejects the attachment itself; that message simply fails."""
        if attachment not in self._media:
            self._media.append(attachment)
            self._media_blobs.extend(b for b in blobs if b not in self._media_blobs)

    @property
    def pending_media(self) -> tuple[tuple[OutboundAttachment, ...], tuple[MediaBlob, ...]]:
        """The carried attachments and their custody handles, not yet in a sent message."""
        return tuple(self._media), tuple(self._media_blobs)

    def media_sent(self) -> None:
        """A message carrying the pending media reached the room."""
        self._media.clear()
        self._media_blobs.clear()

    @property
    def documents_added(self) -> int:
        """How many document links `apply` has added, apart from place links, for the metric."""
        return self._documents_added

    def mark_sent(self, text: str) -> None:
        """A message carrying these documents' links reached the room: they are not added to the
        next message of the turn. A link that was due but not in what was sent (a refusal, a GIF
        with no words) stays pending, so the next message still carries it."""
        if UNSAVED_DOCUMENT_NOTE in (text or ""):
            self._unsaved_noted = True
        self._attached = [url for url in self._attached if url not in (text or "")]
        pattern = self._doc_pattern
        if pattern is None or not self._doc_receipts:
            return
        for match in pattern.finditer(text or ""):
            self._doc_receipts.pop(match.group(1).lower(), None)

    def scrub(self, text: str) -> str:
        """`text` with the document links no tool handed her removed, and nothing added: for the
        copy of a reply that is recorded rather than sent (a GIF caption, its audit row)."""
        if not text or self._doc_pattern is None:
            return text
        return self._remove_documents(text, self._doc_pattern)[0]

    @property
    def stripped(self) -> int:
        """How many made-up document links `apply` has removed, for the metric."""
        return self._stripped

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

    def apply(self, text: str, *, final: bool = False) -> tuple[str, int]:
        """`text` with every earned-but-missing link appended, and how many were added.

        A link is EARNED when the reply names the place (case-folded substring
        of one of its anchors) and MISSING when the url is not already in the
        reply. Appended on their own lines, in offer order, so the shape is the
        one a person sends: the answer, then the links.

        `final` is true for a message the turn ends on, which is where the not-saved note may
        follow a catalog read that was never followed by a publish.

        The count is returned rather than counted internally because the
        `Metrics` instance lives with the `Deliverer`, and giving this class one
        would make it a second thing that knows how to count.
        """
        added = 0
        if text and self._doc_pattern is not None:
            text, added = self._apply_documents(text, self._doc_pattern)
        if text and self._attached:
            missing = [url for url in self._attached if url not in text]
            if missing:
                text = text.rstrip() + "\n" + "\n".join(missing)
                added += len(missing)
        if text and self._offered:
            haystack = text.casefold()
            decoded = unquote_plus(text)
            additions = [
                url for url, anchors in self._offered.items()
                if not _present(url, text, decoded)
                and any(a.casefold() in haystack for a in anchors)
            ]
            if additions:
                log.info("reply_links_carried: %d link(s) the reply had earned and dropped",
                         len(additions))
                text = text.rstrip() + "\n" + "\n".join(additions)
                added += len(additions)
        return self._note_unsaved(text, final), added

    def _note_unsaved(self, text: str, final: bool) -> str:
        """`text` with the not-saved note added once, when `document_publish` was called this
        turn and no call stored a document. The note is true whatever the reply says."""
        if (not text.strip() or not self._document_called or self._document_saved
                or not (self._document_tried or final)
                or self._unsaved_noted or UNSAVED_DOCUMENT_NOTE in text):
            return text
        log.warning("reply_document_not_saved: document_publish was called and stored nothing")
        return text.rstrip() + "\n\n" + UNSAVED_DOCUMENT_NOTE

    def _remove_documents(self, text: str, pattern: re.Pattern[str]) -> tuple[str, int]:
        """`text` without the document links she was not handed, and how many were removed. The
        label of a Markdown link stays when its target goes."""
        removed = 0

        def drop(match: re.Match[str]) -> str:
            nonlocal removed
            if match.group(1).lower() in self._doc_handles:
                return match.group(0)
            removed += 1
            return ""

        # `[label](url)`: a removed target must not leave `[label]()` behind, so the label stays.
        def drop_target(match: re.Match[str]) -> str:
            nonlocal removed
            if match.group(2).lower() in self._doc_handles:
                return match.group(0)
            removed += 1
            return match.group(1)

        markdown = re.compile(r"\[([^\]]*)\]\(\s*" + pattern.pattern + r"\s*\)", re.IGNORECASE)
        text = markdown.sub(drop_target, text)
        text = pattern.sub(drop, text)
        if removed:
            # Only what the removal itself can leave behind: a run of blank lines and the ends.
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text, removed

    def _apply_documents(self, text: str, pattern: re.Pattern[str]) -> tuple[str, int]:
        """Remove every document link she was not handed, then add the link of each document
        published this turn that this reply does not carry and no earlier message did. Returns the
        text and how many links were added."""
        text, removed = self._remove_documents(text, pattern)
        if removed:
            self._stripped += removed
            log.warning("reply_document_links_stripped: %d link(s) she was never handed", removed)
        present = {m.group(1).lower() for m in pattern.finditer(text)}
        missing = [url for handle, url in self._doc_receipts.items() if handle not in present]
        if not missing:
            return text, 0
        self._documents_added += len(missing)
        return text.rstrip() + "\n" + "\n".join(missing), len(missing)


def _present(url: str, text: str, decoded: str) -> bool:
    """Whether the reply already carries `url`, as sent or percent-decoded. A full Maps
    URL with a Chinese place name is mostly `%E9...` escapes, and a model copying it
    often writes the characters themselves (or `+` for a space); read as absent, the
    link would be appended a second time under the model's own copy (ADR 0023)."""
    return url in text or unquote_plus(url) in decoded
