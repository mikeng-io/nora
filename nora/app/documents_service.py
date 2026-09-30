"""Publishing a document, and who may read one (ADR 0020, design/artifact §5, §16).

`DocumentService.publish` is the ONE path a model-written document takes into the
store, and the order is the point, because each step decides what the next may trust:

1. `check_document` judges the bytes (`nora.documents.check`). A refusal carries
   findings; a failure of the checker itself is `unavailable`, never a pass.
2. Every `Image` names a picture by a media code. The code is resolved WITHIN THIS
   ROOM and to exactly one picture, then rewritten to that picture's full sha256:
   the stored document holds content addresses, never the short code a model typed.
3. The rewritten document is checked AGAIN, so what is stored is what was judged, and
   its canonical form, hash and search text are the ones the checker computed.
4. A new document or a new revision is written. A revision must be made in the room
   the document was made in, so a room can never overwrite another room's document.

MEDIA IN A ROOM. A code resolves against the pictures in view in that room, the way
`media_fetch` resolves one: the room's own message rows (`messages.media_json`, which
is where an inbound photo lives, and is the same-room rule for free: a photo sent to
room A is not in room B's rows), and the media index rows born in this room
(`provenance.platform` and `provenance.conversation_id`: a picture Nora generated, or
one the computer registered). A code that matches nothing is refused; a code that
matches more than one picture is refused and the model is told to use a longer one. It
never picks. A row with no room on it (a sticker, a comprehension-born row) cannot be
placed, so it never resolves: the rule fails closed.

`rooms_named` answers the other question, for the read side: which rooms name a
person, from the CURRENT policy, so a removal revokes access. A `persons = "*"` room
names nobody (the policy cannot say who is in it), so only a document's creator reads
what was made there.
"""

from __future__ import annotations

import copy
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from nora.documents import catalog_prompt
from nora.documents.check import Checked, Finding, check_document
from nora.domain.documents import DocumentStore, RevisionIn, RoomKey
from nora.domain.media import code_matches, parse_media_type, resolve_room_media, room_sightings
from nora.domain.ports import MediaObjectStore, RecordStore
from nora.policy import Policy

_HEX = re.compile(r"^[0-9a-f]{6,64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_HANDLES_SHOWN = 5


def _wall_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class PublishResult:
    """`ok` carries the id, revision and portal path. `refused` carries findings the
    model can act on. `conflict` means someone revised the document a moment ago.
    `unavailable` means nothing is known to have been stored; say so, never "done"."""

    status: Literal["ok", "refused", "conflict", "unavailable"]
    document_id: UUID | None = None
    revision: int = 0
    url: str = ""
    findings: tuple[Finding, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class ReadResult:
    """A document as its room sees it: the current revision and its spec, so a model
    can apply a change to what is stored. `refused` with `no_such_document` is the one
    answer for an unknown id and for a document made in another room."""

    status: Literal["ok", "refused", "unavailable"]
    document_id: UUID | None = None
    revision: int = 0
    title: str = ""
    spec: dict[str, Any] | None = None
    url: str = ""
    findings: tuple[Finding, ...] = ()
    note: str = ""


def rooms_named(policy: Policy, person_id: str) -> frozenset[RoomKey]:
    """Every `(platform, conversation_id)` whose room names `person_id`, every id a
    room answers to included. A `"*"` room is left out: it names nobody."""
    return frozenset(
        (convo.platform, cid)
        for convo in policy.conversations
        if convo.persons is not None and person_id in convo.persons
        for cid in convo.ids
    )


def _images(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every Image node and its JSON Pointer. Iterative: the document has passed the
    checker, so depth is bounded, but nothing here needs to rely on that."""
    found: list[tuple[str, dict[str, Any]]] = []
    stack: list[tuple[dict[str, Any], str]] = [(document["root"], "/root")]
    while stack:
        node, where = stack.pop()
        if node.get("type") == "Image":
            found.append((where, node))
        for index, child in enumerate(node.get("children") or []):
            stack.append((child, f"{where}/children/{index}"))
    return sorted(found, key=lambda pair: pair[0])


class DocumentService:
    def __init__(
        self,
        *,
        store: DocumentStore,
        records: RecordStore,
        media: MediaObjectStore,
        media_window: int,
        room_ids: Callable[[str, str], frozenset[str]],
        is_person: Callable[[str], bool],
        new_id: Callable[[], UUID] = uuid4,
        now_ms: Callable[[], int] = _wall_ms,
    ) -> None:
        self._store = store
        self._records = records
        self._media = media
        self._window = media_window
        # Every id a room answers to (`Conversation.ids`): a room that answers to two ids
        # is one room, for reading its pictures and for revising its own document.
        self._room_ids = room_ids
        # Whether `person_id` is a resolved, known person. `created_by` decides who may
        # read a document in a room that does not name them, so a raw platform id that
        # happens to equal a real person id must never reach it (review of PR 654).
        self._is_person = is_person
        self._new_id = new_id
        self._now_ms = now_ms

    # ------------------------------------------------------------------ media
    async def _sightings(self, platform: str, conversation_id: str, code: str) -> set[str] | None:
        """The distinct full sha256 of every picture in this room whose sha256
        starts with `code`, or `None` when the room could not be read, or when the
        scan was cut short and found nothing (`could not check that far` is never
        `no such picture`)."""
        found: set[str] = set()
        truncated = False
        try:
            ids = self._room_ids(platform, conversation_id) | {conversation_id}
            now = self._now_ms()
            for cid in sorted(ids):
                # The one resolver the sandbox session shares (DEC-251): the recent window
                # plus the room's last 90 days of message rows, keyed by the message row.
                seen = await room_sightings(
                    self._records, conversation_id=cid, now_ms=now, window=self._window
                )
                truncated = truncated or seen.truncated
                for sha, sighting in code_matches(seen.sightings, code).items():
                    # Images only (this block's own rule): a clip or a voice note is not a
                    # picture, and a shared item's cover is a preview of someone else's post.
                    if (
                        _SHA.match(sha)
                        and not sighting.cover
                        and parse_media_type(sighting.media_type)[0] == "image"
                    ):
                        found.add(sha)
                # What Nora made or kept in this room is in the index, not in a message row.
                look = await resolve_room_media(
                    self._media, code=code, platform=platform, conversation_id=cid
                )
                if not look.available:
                    return None
                found.update(sha for sha in look.objects if _SHA.match(sha))
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`, not a guess
            return None
        if truncated and not found:
            return None
        return found

    async def _resolve(
        self, platform: str, conversation_id: str, document: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[str, ...], list[Finding], bool]:
        """Rewrite every Image's code to its full sha256. Returns the rewritten
        document, the shas it names, the findings, and whether a store was down."""
        rewritten = copy.deepcopy(document)
        findings: list[Finding] = []
        named: list[str] = []
        cache: dict[str, str | None] = {}
        for where, node in _images(rewritten):
            code = str(node["props"].get("media", ""))
            path = f"{where}/props/media"
            if not _HEX.match(code):
                findings.append(
                    Finding(path, "media_code", "must be a media code: 6 to 64 hex characters")
                )
                continue
            if code not in cache:
                seen = await self._sightings(platform, conversation_id, code)
                if seen is None:
                    return document, (), [], True
                if len(seen) == 1:
                    cache[code] = next(iter(seen))
                else:
                    cache[code] = None
                    shown = ", ".join(sorted(s[:8] for s in seen)[:_HANDLES_SHOWN])
                    if not seen:
                        message = f"no picture with code {code} in this room's recent messages"
                        findings.append(Finding(path, "media_unknown", message))
                    else:
                        message = (
                            f"{code} matches {len(seen)} pictures in this room ({shown}); "
                            "use a longer code"
                        )
                        findings.append(Finding(path, "media_ambiguous", message))
                    continue
            sha = cache[code]
            if sha is None:
                findings.append(
                    Finding(path, "media_unknown", f"no single picture for code {code}")
                )
            else:
                node["props"]["media"] = sha
                named.append(sha)
        return rewritten, tuple(sorted(set(named))), findings, False

    # ------------------------------------------------------------ catalog, read
    def catalog(self) -> str:
        """The catalog prompt the checker's schema was generated with, so a model
        that has not seen the catalog can learn it from the tool, not from memory."""
        return catalog_prompt()

    async def read(self, *, platform: str, conversation_id: str, document_id: UUID) -> ReadResult:
        """The current revision of a document made in THIS room. Another room's
        document answers exactly like an unknown id (`no_such_document`)."""
        try:
            current = await self._store.get(document_id=document_id)
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`
            return ReadResult("unavailable", note="The document could not be read right now.")
        here = self._room_ids(platform, conversation_id) | {conversation_id}
        if (
            current is None
            or current.summary.platform != platform
            or current.summary.conversation_id not in here
        ):
            return ReadResult("refused", findings=_no_such_document().findings)
        return ReadResult(
            "ok",
            document_id=document_id,
            revision=current.summary.revision,
            title=current.summary.title,
            spec=current.spec,
            url=f"/documents/{document_id}",
        )

    # ---------------------------------------------------------------- publish
    async def publish(
        self,
        *,
        platform: str,
        conversation_id: str,
        person_id: str,
        raw: bytes | str | dict[str, Any],
        written_by: str,
        document_id: UUID | None = None,
        base_revision: int | None = None,
    ) -> PublishResult:
        if not self._is_person(person_id):
            return PublishResult(
                "refused",
                findings=(
                    Finding("", "unknown_person", "only a known member can publish a document"),
                ),
            )
        if document_id is not None and base_revision is None:
            # A revision that does not say which revision it started from would silently
            # replace newer work (review of PR 654: a stale edit won as revision 3).
            return PublishResult(
                "refused",
                findings=(
                    Finding(
                        "",
                        "base_revision_required",
                        "say which revision you started from: the `revision` of the document "
                        "you read",
                    ),
                ),
            )
        checked = check_document(raw)
        if checked.status == "error":
            return PublishResult(
                "unavailable", note="The document could not be checked, so it was not saved."
            )
        if not checked.ok or checked.document is None:
            return PublishResult("refused", findings=checked.findings)

        rewritten, media, findings, down = await self._resolve(
            platform, conversation_id, checked.document
        )
        if down:
            return PublishResult(
                "unavailable", note="The room's pictures could not be read, so nothing was saved."
            )
        if findings:
            return PublishResult("refused", findings=tuple(findings))
        final: Checked = check_document(rewritten)
        if not final.ok or final.document is None:
            return PublishResult(
                "unavailable", note="The document could not be re-checked, so it was not saved."
            )
        revision = RevisionIn(
            title=final.title,
            spec=final.document,
            text=final.text,
            sha256=final.sha256,
            media=media,
            written_by=written_by,
        )

        try:
            if document_id is None:
                made = await self._store.create(
                    document_id=self._new_id(),
                    platform=platform,
                    conversation_id=conversation_id,
                    created_by=person_id,
                    revision=revision,
                )
                return _ok(made.summary.id, made.summary.revision)
            current = await self._store.get(document_id=document_id)
            # A document in another room is answered exactly like one that does not exist:
            # a model in room B must not learn that an id is a document in room A (the read
            # API never distinguishes them either).
            here = self._room_ids(platform, conversation_id) | {conversation_id}
            if (
                current is None
                or current.summary.platform != platform
                or current.summary.conversation_id not in here
            ):
                return _no_such_document()
            if current.summary.revision != base_revision:
                return PublishResult(
                    "conflict",
                    document_id=document_id,
                    revision=current.summary.revision,
                    note=(
                        f"This document is at revision {current.summary.revision}, not "
                        f"{base_revision}. Read it again, apply your change to that, and "
                        "publish again."
                    ),
                )
            outcome = await self._store.add_revision(
                document_id=document_id,
                base_revision=current.summary.revision,
                revision=revision,
            )
        except Exception:  # noqa: BLE001 - a store that is down is `unavailable`
            return PublishResult(
                "unavailable", note="The document could not be saved; nothing was stored."
            )
        if outcome.status == "conflict":
            return PublishResult(
                "conflict",
                document_id=document_id,
                note="Someone revised this document a moment ago. Read it again and publish again.",
            )
        if outcome.status == "not_found" or outcome.document is None:
            return _no_such_document()
        return _ok(document_id, outcome.document.summary.revision)


def _no_such_document() -> PublishResult:
    return PublishResult(
        "refused",
        findings=(Finding("", "no_such_document", "no document with that id in this room"),),
    )


def _ok(document_id: UUID, revision: int) -> PublishResult:
    return PublishResult(
        "ok", document_id=document_id, revision=revision, url=f"/documents/{document_id}"
    )


__all__ = ["DocumentService", "PublishResult", "ReadResult", "rooms_named"]
