from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from types import SimpleNamespace
from typing import Any, Literal, cast

from nora.agent.toolkit.results import HELD, _send_status
from nora.computer.adapters.tools.shared import (
    _COMPUTER_OFF,
    _COMPUTER_UNRECORDED,
    _EXPORT_TITLE_CHARS,
)
from nora.domain.computer import (
    SandboxStatus,
)
from nora.domain.computer_events import (
    CANCELLED,
)
from nora.domain.computer_exports import (
    NOT_AS_LINK,
    ComputerExport,
    ComputerExportStore,
    mint_artifact_id,
    sniff_export_type,
)
from nora.domain.computer_sessions import (
    cut,
)
from nora.domain.media import (
    COMPUTER_MEDIA_SOURCE,
    HANDLE_CHARS,
    MediaBlob,
    MediaObject,
    born_in_room,
    label_from_media_type,
)
from nora.domain.ports import (
    OutboundAttachment,
)
from nora.obs.logging import current_tag_id

log = logging.getLogger("nora.tools")




def _make_computer_export(deps):
    async def computer_export(
        path: str,
        to: Literal["link", "chat", "keep"],
        title: str | None = None,
        artifact_id: str | None = None,
    ) -> dict:
        """Send a file from your computer out as a link, a chat attachment or
        a kept copy. Call this when a file you made is ready.

        MUST: it sends any kind of file (images, charts, PDF, data, audio,
        video, archives), never a web page. For many files, put them in one
        zip on your computer and send that.

        A link opens the file in the portal for people in this room who
        have signed in; for anyone else, send it to chat.

        Returns `status`: ok, nothing_found, refused, held_for_review or
        unavailable, with a `note` on how to read it.
        - ok: sent. A link carries `url` and `artifact_id`; a kept picture
          its media `code`.
        - nothing_found: there is no such file.
        - refused: too large, a web page, an SVG as a link, an artifact_id
          this room did not make, or nobody here asked for it.
        - held_for_review: made, and held by review or this room's cooldown.
        - unavailable: the computer, or where it was going, could not be reached.

        Args:
            path: The file, relative to the workspace folder.
            to: link gives a portal link that opens the file; chat attaches
                it here; keep stores it privately and sends nothing.
            title: The name people see. Defaults to the file name.
            artifact_id: Only with to=link: the id of a link this room made,
                to replace its file and keep the same link.
        """

        c = SimpleNamespace(
            path=path,
            to=to,
            title=title,
            artifact_id=artifact_id,
            artifact_link_base=deps.artifact_link_base,
            blob_store=deps.blob_store,
            computer=deps.computer,
            computer_exports=deps.computer_exports,
            deliverer=deps.deliverer,
            _computer_begin=deps.computer_ctx._computer_begin,
            _computer_finish=deps.computer_ctx._computer_finish,
            _computer_off=deps.computer_ctx._computer_off,
            _computer_session=deps.computer_ctx._computer_session,
            _export_record=_make__export_record(deps),
            _kept_image_code=_make__kept_image_code(deps),
            _new_artifact_id=_make__new_artifact_id(deps),
            bound_computer_scope=deps.computer_ctx.bound_computer_scope,
            bound_conversation_id=deps.bound_conversation_id,
            computer_actor=deps.computer_ctx.computer_actor,
            computer_bounds=deps.computer_ctx.computer_bounds,
            computer_platform=deps.computer_ctx.computer_platform,
            computer_ready=deps.computer_ctx.computer_ready,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await _computer_export__begin(c)
        if k_:
            return v_
        try:
            k_, _, v_ = await _computer_export__resolve_target(c)
            if k_:
                return v_
            k_, _, v_ = await _computer_export__stage_file(c)
            if k_:
                return v_
            k_, _, v_ = await _computer_export__check_delivery(c)
            if k_:
                return v_
        except BaseException as _e4:  # noqa: BLE001
            c.exc = _e4
            k_, _, v_ = await _computer_export__fail(c)
            if k_:
                return v_
        finally:
            await _computer_export__finish(c)
        return None  # type: ignore[return-value]

    return computer_export


def _make__new_artifact_id(deps):
    computer_exports = deps.computer_exports

    async def _new_artifact_id() -> str | None:
        """A fresh `artifact_id` no row holds yet, or None when three draws"""
        exports = cast(ComputerExportStore, computer_exports)  # checked wired by the caller
        for _ in range(3):
            candidate = mint_artifact_id()
            try:
                if await exports.for_artifact(artifact_id=candidate) is None:
                    return candidate
            except Exception:
                log.exception("computer_export: artifact_id check failed")
                return None
        return None

    return _new_artifact_id


def _make__kept_image_code(deps):
    media_store = deps.media_store
    metrics = deps.metrics
    bound_conversation_id = deps.bound_conversation_id
    computer_platform = deps.computer_ctx.computer_platform
    now_fn = deps.now_fn

    async def _kept_image_code(
        key: str,
        media_type: str,
        size: int,
        title: str,
        path: str,
    ) -> str | None:
        """Index a kept image in the media store, born in this room, and"""
        if media_store is None:
            return None
        obj = MediaObject(
            sha256=key,
            media_type=media_type,
            size=size,
            source=COMPUTER_MEDIA_SOURCE,
            # Seeded like `generate_image`'s: Nora made it, so what it is needs
            # no vision call. `describe_model` stays empty.
            description=title,
            title=title,
            provenance={
                "title": title,
                "path": path,
                "platform": computer_platform,
                "conversation_id": bound_conversation_id,
                "tag_id": current_tag_id(),
            },
            created_at_ms=int(now_fn().timestamp() * 1000),
        )
        try:
            await media_store.save(obj=obj)
            # First source wins: when these bytes were indexed before, the row
            # is that first source's, and the code is this room's only if
            # that row was born here. Read back rather than trusting `save`.
            held = await media_store.get(sha256=key)
            if held is None or not born_in_room(
                held,
                platform=computer_platform,
                conversation_id=bound_conversation_id,
            ):
                return None
        except Exception:
            log.exception("computer_export: media_store save failed")
            metrics.inc("nora_computer_audit_failures_total", tool="computer_export", stage="media")
            return None
        return obj.handle

    return _kept_image_code


def _make__export_record(deps):
    computer_exports = deps.computer_exports
    metrics = deps.metrics

    async def _export_record(tool: str, export: ComputerExport) -> bool:
        """Record one export that reached its destination. A failure is logged"""
        if computer_exports is None:
            return False
        try:
            await computer_exports.record(export)
        except Exception:
            log.exception("computer_exports record failed (tool=%s)", tool)
            metrics.inc("nora_computer_audit_failures_total", tool=tool, stage="export")
            return False
        return True

    return _export_record


async def _computer_export__begin(c):
    c.tool = "computer_export"
    c.started = c.now_fn()
    c.operation_id = uuid.uuid4()
    c.request: dict[str, object] = {
        "path": c.path,
        "to": c.to,
        "title": c.title,
        "artifact_id": c.artifact_id,
    }
    if not c.computer_ready or c.computer is None:
        await c._computer_off(c.tool, c.operation_id, started=c.started, request=c.request)
        return (True, None, dict(_COMPUTER_OFF))
    c.session_id = await c._computer_session(c.tool)
    if not await c._computer_begin(
        c.tool, c.operation_id, started=c.started, request=c.request, session_id=c.session_id
    ):
        return (True, None, dict(_COMPUTER_UNRECORDED))
    c.cap = c.computer_bounds.export_max_bytes
    c.outcome = CANCELLED
    c.row: dict[str, object] = {"to": c.to, "reason": "cancelled before the export finished"}
    return (False, None, None)


async def _computer_export__resolve_target(c):
    if c.to not in ("link", "chat", "keep"):
        c.outcome, c.row = ("refused", {"to": c.to, "reason": "unknown destination"})
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": "to is link, chat or keep",
                "note": ("Nothing was sent. Say where it should go: link, chat or keep."),
            },
        )
    if c.artifact_id and c.to != "link":
        c.outcome, c.row = ("refused", {"to": c.to, "reason": "artifact_id without a link"})
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": "artifact_id is only for to=link",
                ("note"): "Nothing was sent: artifact_id replaces a link's file, so it goes with "
                "to=link only.",
            },
        )
    if c.to != "keep" and c.computer_actor != "tag":
        c.outcome, c.row = ("refused", {"to": c.to, "reason": "no person asked"})
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": "nobody asked for it in this turn",
                (
                    "note"
                ): "Nothing was sent: a link or an attachment goes out only when somebody asked "
                "for it now. Keep it instead, or wait to be asked.",
            },
        )
    if (
        (c.to == "link" and (not c.artifact_link_base or c.computer_exports is None))
        or (c.to == ("keep") and c.computer_exports is None)
        or (c.to == "chat" and c.deliverer is None)
    ):
        c.outcome, c.row = ("unavailable", {"to": c.to, "reason": "destination not wired"})
        return (
            True,
            None,
            {
                "status": "unavailable",
                "reason": f"{c.to} is not available here",
                ("note"): "Nothing was sent: that way out is switched off here. Say so, or offer "
                "another.",
            },
        )
    c.owner: ComputerExport | None = None
    if c.artifact_id:
        try:
            c.exports = cast(ComputerExportStore, c.computer_exports)
            c.owner = await c.exports.for_artifact(artifact_id=c.artifact_id)
        except Exception:
            log.exception("computer_export: owner read failed")
            c.outcome, c.row = ("unavailable", {"to": c.to, "reason": "owner unreadable"})
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "reason": ("the link's record could not be read"),
                    "note": "Nothing was changed: I couldn't check whose link that is. "
                    "Say so; don't claim the link was updated.",
                },
            )
        if c.owner is None or (c.owner.platform, c.owner.conversation_id) != (
            c.computer_platform,
            c.bound_conversation_id,
        ):
            c.outcome, c.row = ("refused", {"to": c.to, "reason": "not this room's artifact"})
            return (
                True,
                None,
                {
                    "status": "refused",
                    "reason": "not a link this room made",
                    "note": (
                        "Nothing was changed: that link was not made from this room's computer. "
                        "Export "
                        "without artifact_id for a new link."
                    ),
                },
            )
    return await _computer_export__fetch(c)


async def _computer_export__fetch(c):
    c.fetched = await c.computer.fetch(scope=c.bound_computer_scope, path=c.path, max_bytes=c.cap)
    if c.fetched.status is SandboxStatus.NOTHING_FOUND:
        c.outcome, c.row = ("nothing_found", {"to": c.to, "reason": c.fetched.reason})
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "reason": c.fetched.reason or "",
                "note": (
                    "There is no such file in the workspace. List it with computer_run before "
                    "guessing "
                    "another name."
                ),
            },
        )
    if c.fetched.status is SandboxStatus.DENIED:
        c.outcome, c.row = ("refused", {"to": c.to, "reason": c.fetched.reason})
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": c.fetched.reason or "",
                "note": (
                    f"Nothing was sent: the file is outside the workspace or over the "
                    f"{c.cap // (1024 * 1024)} MB limit. Make it smaller or use a path inside the "
                    f"workspace."
                ),
            },
        )
    if c.fetched.status is not SandboxStatus.OK:
        c.outcome, c.row = ("unavailable", {"to": c.to, "reason": c.fetched.reason})
        return (
            True,
            None,
            {
                "status": "unavailable",
                "reason": c.fetched.reason or "",
                "note": (
                    "Nothing was sent: the computer could not be reached. Say so; don't claim it "
                    "went out."
                ),
            },
        )
    c.data = c.fetched.data
    c.digest = hashlib.sha256(c.data).hexdigest()
    c.received = {
        "to": c.to,
        "size": len(c.data),
        "sha256": c.digest,
        "claimed_sha256": c.fetched.sha256,
    }
    if c.fetched.sha256 is not None and c.fetched.sha256 != c.digest:
        c.outcome, c.row = ("unavailable", {**c.received, "reason": "digest mismatch"})
        return (
            True,
            None,
            {
                "status": "unavailable",
                "reason": "the file changed on its way out",
                (
                    "note"
                ): "Nothing was sent: what arrived is not what the computer said it sent. Say "
                "so; try once more at most.",
            },
        )
    if len(c.data) > c.cap:
        c.outcome, c.row = ("refused", {**c.received, "reason": "over the cap"})
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": "file is over the cap",
                "note": (
                    f"Nothing was sent: the file is over the {c.cap // (1024 * 1024)} MB limit. "
                    f"Make it "
                    f"smaller."
                ),
            },
        )
    c.media_type = sniff_export_type(c.data, c.path)
    if c.media_type is None:
        c.outcome, c.row = ("refused", {**c.received, "reason": "type not allowed"})
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": "a web page may not leave",
                "note": (
                    "Nothing was sent: this file is a web page (HTML), the one kind of file that "
                    "cannot "
                    "leave your computer. Put its content in a document instead, or send the data."
                ),
            },
        )
    if c.to == "link" and c.media_type in NOT_AS_LINK:
        c.outcome, c.row = (
            "refused",
            {**c.received, "media_type": c.media_type, "reason": ("svg as a link")},
        )
        return (
            True,
            None,
            {
                "status": "refused",
                "reason": "an SVG is never published as a link",
                (
                    "note"
                ): "Nothing was sent: an SVG may be attached here or kept, never linked. Send "
                "it with to=chat instead.",
            },
        )
    return (False, None, None)


async def _computer_export__stage_file(c):
    c.name = c.path.rsplit("/", 1)[-1] or "file"
    c.shown_title = cut((c.title or "").strip() or c.name, _EXPORT_TITLE_CHARS)
    c.received = {**c.received, "media_type": c.media_type, "title": c.shown_title}

    def _export_row(**target: Any) -> ComputerExport:
        return ComputerExport(
            id=uuid.uuid4(),
            created_at=c.now_fn(),
            platform=c.computer_platform,
            conversation_id=c.bound_conversation_id,
            scope=c.bound_computer_scope,
            path=c.path,
            destination=c.to,
            sha256=c.digest,
            size_bytes=len(c.data),
            media_type=c.media_type,
            title=c.shown_title,
            tag_id=current_tag_id(),
            operation_id=c.operation_id,
            session_id=c.session_id,
            **target,
        )

    c._export_row = _export_row
    try:
        c.key = await c.blob_store.put(data=c.data)
    except Exception:
        log.exception("computer_export: blob put failed")
        c.outcome, c.row = ("unavailable", {**c.received, "reason": "blob put failed"})
        return (
            True,
            None,
            {
                "status": "unavailable",
                "reason": "the file could not be stored",
                ("note"): "Nothing was sent: I couldn't store the file. Say so.",
            },
        )
    return await _computer_export__deliver(c)


async def _computer_export__deliver(c):
    if c.to == "link":
        c.made = c.artifact_id or await c._new_artifact_id()
        if c.made is None:
            c.outcome, c.row = ("unavailable", {**c.received, "reason": "no free artifact_id"})
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "reason": "the link could not be made",
                    ("note"): "No link was made. Say so; never write a link you were not given.",
                },
            )
        c.url = f"{c.artifact_link_base.rstrip('/')}/artifacts/{c.made}"
        c.recorded = await c._export_record(
            c.tool,
            c._export_row(
                artifact_id=c.made, url=c.url, revised=bool(c.artifact_id), blob_key=c.key
            ),
        )
        if not c.recorded:
            c.outcome, c.row = (
                "unavailable",
                {**c.received, "blob_key": c.key, "reason": ("not recorded")},
            )
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    "reason": "the link could not be made",
                    (
                        "note"
                    ): "No link was made: I couldn't record it. Say so; never write a link you "
                    "were not given.",
                },
            )
        c.outcome, c.row = (
            "ok",
            {
                **c.received,
                "artifact_id": c.made,
                "url": c.url,
                "revised": bool(c.artifact_id),
                ("blob_key"): c.key,
                "recorded": c.recorded,
            },
        )
        return (
            True,
            None,
            {
                "status": "ok",
                "url": c.url,
                "artifact_id": c.made,
                "sha256": c.digest,
                ("media_type"): c.media_type,
                "size": len(c.data),
                "note": "The link opens the file in "
                "the portal for people in this room who have signed in. Put it in your reply in "
                "your own words. To change the file later and keep this link, export again "
                "with this artifact_id.",
            },
        )
    if c.to == "keep":
        c.recorded = await c._export_record(c.tool, c._export_row(blob_key=c.key))
        c.code = (
            await c._kept_image_code(c.key, c.media_type, len(c.data), c.shown_title, c.path)
            if c.media_type.startswith("image/")
            else None
        )
        c.outcome, c.row = (
            "ok",
            {**c.received, "blob_key": c.key, "recorded": c.recorded, "code": c.code},
        )
        if c.code:
            return (
                True,
                None,
                {
                    "status": "ok",
                    "kept": True,
                    "code": c.code,
                    "sha256": c.digest,
                    ("media_type"): c.media_type,
                    "size": len(c.data),
                    "note": "Kept privately; nobody "
                    "was sent anything. Its code puts this picture in a document in this room. "
                    "Don't tell them it was shared.",
                },
            )
        return (
            True,
            None,
            {
                "status": "ok",
                "kept": True,
                "sha256": c.digest,
                "media_type": c.media_type,
                ("size"): len(c.data),
                "note": "Kept privately; nobody was sent anything. Don't tell them it was shared.",
            },
        )
    return await _computer_export__deliver_chat(c)


async def _computer_export__deliver_chat(c):
    c.label = label_from_media_type(c.media_type)
    c.held_before = c.deliverer.held_count
    c.sent = await c.deliverer.deliver(
        "",
        source="tool",
        attachments=(OutboundAttachment(content=c.data, media_type=c.media_type, filename=c.name),),
        record_text=(f"[{c.label} {c.digest[:HANDLE_CHARS]}] (from your computer)"),
        record_metadata={"media": {"type": c.label, "sha256": c.digest, "computer_export": True}},
        media_blobs=(
            MediaBlob(sha256=c.key, media_type=c.media_type, size=len(c.data), filename=c.name),
        ),
    )
    c.sent_status = _send_status(c.deliverer, sent=c.sent, held_before=c.held_before)
    if c.sent_status == HELD:
        c.outcome, c.row = (HELD, {**c.received, "blob_key": c.key})
        return (
            True,
            None,
            {
                "status": "held_for_review",
                "note": (
                    "The file is ready and my part "
                    "is DONE. A review mode or this room's cooldown held the send. Do not send it "
                    "again."
                ),
            },
        )
    return (False, None, None)


async def _computer_export__check_delivery(c):
    if not c.sent:
        c.outcome, c.row = (
            "unavailable",
            {**c.received, "blob_key": c.key, "reason": ("not delivered")},
        )
        return (
            True,
            None,
            {
                "status": "unavailable",
                "reason": "the attachment was not sent",
                (
                    "note"
                ): "The file did not reach this chat. Say so plainly; don't claim it arrived.",
            },
        )
    c.recorded = await c._export_record(c.tool, c._export_row(blob_key=c.key))
    c.outcome, c.row = ("ok", {**c.received, "blob_key": c.key, "recorded": c.recorded})
    return (
        True,
        None,
        {
            "status": "ok",
            "sent": True,
            "media_type": c.media_type,
            "sha256": c.digest,
            ("size"): len(c.data),
            "note": "The file is already in this chat. Say what it is in your "
            "own words; don't send it again.",
        },
    )
    return (False, None, None)


async def _computer_export__fail(c):
    if not isinstance(c.exc, asyncio.CancelledError):
        c.outcome, c.row = ("unavailable", {**c.row, "reason": type(c.exc).__name__})
    raise
    return (False, None, None)


async def _computer_export__finish(c):
    await c._computer_finish(
        c.tool,
        c.operation_id,
        c.outcome,
        started=c.started,
        finished=c.now_fn(),
        result=c.row,
        session_id=c.session_id,
    )
    return (False, None, None)

