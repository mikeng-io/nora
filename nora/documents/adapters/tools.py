from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID

from nora.agent.toolkit.pages import _without_map_coordinates
from nora.agent.toolkit.results import _record
from nora.agent.toolkit.spec import _spec
from nora.domain.documents import is_handle as _is_document_handle
from nora.obs.logging import current_tag_id

log = logging.getLogger("nora.tools")


_DOCUMENT_READ_CHARS = 40_000


def _make__document_findings(deps):
    def _document_findings(findings: Sequence[Any]) -> list[str]:
        """The checker's findings as plain sentences, each led by where it is."""
        return [f"{f.path}: {f.message}" if f.path else str(f.message) for f in findings]

    return _document_findings


def _make__document_id(deps):
    def _document_id(raw: str | None) -> UUID | None:
        try:
            return UUID(str(raw).strip())
        except (ValueError, TypeError, AttributeError):
            return None

    return _document_id


def _make_document_publish(deps):
    async def document_publish(
        spec: dict[str, Any] | str | None = None,
        document_id: str | None = None,
        base_revision: int | None = None,
    ) -> dict:
        """Publish a document to the web app, a plan, report or itinerary people
        open later. Call this when somebody asks for one. Returns where to open it.

        MUST: send the document as a JSON object in `spec`, never HTML, markup
        or a file on your computer. Call once with no `spec` to read the
        component catalog first.

        MUST: to change a document, pass its `document_id` and the `revision`
        `document_read` gave you as `base_revision`.

        It is not a public link: people open it in the web app, signed in. A
        document exists only once a call returns `ok`.

        `status` is one of:
        - `ok`: stored; it carries `url` and `revision`.
        - `bad_arguments`: nothing stored; the findings say what to fix, or the
          revision is stale.
        - `not_found`: no such document in this room.
        - `unavailable`: not stored.
        Every result carries a `note` saying how to report it.

        Args:
            spec: The document, one JSON object. Leave out to read the catalog.
            document_id: The document to change. Leave out for a new one.
            base_revision: The revision you read, required with `document_id`.
        """
        c = SimpleNamespace(
            spec=spec,
            document_id=document_id,
            base_revision=base_revision,
            documents=deps.documents,
            metrics=deps.metrics,
            room=deps.room,
            subagent_job=deps.subagent_job,
            _disclosed_live_addresses=deps._disclosed_live_addresses,
            _document_findings=_make__document_findings(deps),
            _document_id=_make__document_id(deps),
            _route_person_names=deps._route_person_names,
            bound_conversation_id=deps.bound_conversation_id,
            document_actor=deps.document_actor,
            document_asked=deps.document_asked,
            link_carry=deps.link_carry,
        )
        k_, _, v_ = await _document_publish__p1(c)
        if k_:
            return v_
        k_, _, v_ = await _document_publish__p2(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return document_publish


def _make_document_read(deps):
    documents = deps.documents
    metrics = deps.metrics
    room = deps.room
    _document_id = _make__document_id(deps)
    bound_conversation_id = deps.bound_conversation_id
    link_carry = deps.link_carry

    async def document_read(document_id: str) -> dict:
        """Read a document made in this room: its current `revision` and `spec`.
        Call this before changing a document, or to see what you published.

        Args:
            document_id: The `document_id` a publish gave you, or the 12-hex code
                at the end of its link.
        """
        tool = "document_read"
        if documents is None:
            _record(metrics, tool, "unavailable", reason="not_wired")
            return {
                "status": "unavailable",
                "note": "I can't read documents right now. Say you could not check.",
            }
        target = _document_id(document_id)
        handle = str(document_id or "").strip().lower()
        if target is None and not _is_document_handle(handle):
            _record(metrics, tool, "not_found", reason="bad_id")
            return {
                "status": "not_found",
                "note": "That is not a document id, so nothing was read.",
            }
        # The person who asked: a document is read as its creator, or as a person its room
        # names, never by room membership alone (8765_44827). Nobody asked, nothing is read.
        tagger = getattr(room, "tagger", None) if room is not None else None
        try:
            out = await documents.read(
                platform=room.platform if room is not None else "",
                conversation_id=bound_conversation_id,
                document_id=target,
                handle="" if target is not None else handle,
                person_id=tagger.person_id if tagger is not None else None,
            )
        except Exception:
            log.exception("document_read failed")
            _record(metrics, tool, "unavailable", reason="raised")
            return {
                "status": "unavailable",
                "note": "I could not read it. Say you could not check.",
            }
        if out.status == "refused":
            _record(metrics, tool, "not_found")
            return {
                "status": "not_found",
                "note": "There is no document with that id in this room.",
            }
        if out.status != "ok" or out.spec is None:
            _record(metrics, tool, "unavailable", reason=out.status)
            return {
                "status": "unavailable",
                "note": "I could not read it. Say you could not check.",
            }
        _record(metrics, tool, "ok", revision=out.revision)
        body = json.dumps(out.spec, ensure_ascii=False, separators=(",", ":"))
        result: dict[str, Any] = {
            "status": "ok",
            "document_id": str(out.document_id),
            "revision": out.revision,
            "title": out.title,
        }
        if out.url:  # no public origin configured = no link, and none is invented
            link_carry.mint_document(out.url, published=False)
            result["url"] = out.url
            result["link_note"] = (
                "You may repeat this `url` exactly as given; a document link "
                "you did not get from a tool this turn is removed."
            )
        if getattr(out, "state", "active") != "active":
            result["state"] = out.state
        if len(body) > _DOCUMENT_READ_CHARS:
            return {
                **result,
                "note": "It is too large to show here, so `spec` is left out. "
                "To change it, send the whole document again with "
                "`base_revision` set to this `revision`.",
            }
        return {
            **result,
            "spec": _without_map_coordinates(out.spec),
            "note": "This is the current revision. Apply the change to this spec "
            "and publish it with `base_revision` set to `revision`.",
        }

    return document_read


def _make_document_set_status(deps):
    documents = deps.documents
    metrics = deps.metrics
    room = deps.room
    _document_id = _make__document_id(deps)
    bound_conversation_id = deps.bound_conversation_id
    document_actor = deps.document_actor
    link_carry = deps.link_carry

    async def document_set_status(
        document_id: str,
        state: Literal["archived", "deleted", "active"],
    ) -> dict:
        """Archive, delete or restore a whole document made in this room. Call this
        when somebody asks to archive, delete or bring back a document.

        `archived` takes it off the documents list; its link still opens it, and
        it cannot be changed until restored. `deleted` takes it off every list
        and its link stops opening; nothing is erased. `active` restores either
        one. Your documents view lists put-away documents with their ids.

        Returns `status`: ok, not_found or unavailable, with a `note`.

        Args:
            document_id: The document's id from your documents view, or the
                12-hex code at the end of its link.
            state: The state to put it in.
        """
        tool = "document_set_status"
        if documents is None:
            _record(metrics, tool, "unavailable", reason="not_wired")
            return {
                "status": "unavailable",
                "note": "I can't change documents right now. Say so; nothing changed.",
            }
        tagger = getattr(room, "tagger", None) if room is not None else None
        if document_actor != "tag" or tagger is None:
            _record(metrics, tool, "unavailable", reason="no_requester")
            return {
                "status": "unavailable",
                "note": "A document's state is changed for the person who asked, "
                "and nobody asked here, so nothing changed.",
            }
        target = _document_id(document_id)
        handle = str(document_id or "").strip().lower()
        if target is None and not _is_document_handle(handle):
            _record(metrics, tool, "not_found", reason="bad_id")
            return {"status": "not_found", "note": "That is not a document id, so nothing changed."}
        try:
            out = await documents.set_state(
                platform=room.platform,
                conversation_id=bound_conversation_id,
                person_id=tagger.person_id,
                document_id=target,
                state=state,
                handle="" if target is not None else handle,
            )
        except Exception:
            log.exception("document_set_status failed")
            _record(metrics, tool, "unavailable", reason="raised")
            return {
                "status": "unavailable",
                "note": "It could not be changed, so nothing changed. Say you could not.",
            }
        rules = [f.rule for f in out.findings]
        if out.status == "refused" and "no_such_document" in rules:
            _record(metrics, tool, "not_found")
            return {
                "status": "not_found",
                "note": "There is no document with that id in this room. Nothing changed.",
            }
        if out.status != "ok":
            _record(metrics, tool, "unavailable", reason=rules[0] if rules else out.status)
            return {
                "status": "unavailable",
                "note": "It could not be changed, so nothing changed. Say you could not.",
            }
        _record(metrics, tool, "ok", state=out.state)
        notes = {
            "archived": "Archived. It is off the documents list; its link still "
            "opens it. Setting it active brings it back.",
            "deleted": "Deleted. It is off every list and its link no longer opens. "
            "Nothing was erased: setting it active brings it back.",
            "active": "Active again: listed, and it can be changed.",
        }
        result: dict[str, Any] = {
            "status": "ok",
            "document_id": str(out.document_id),
            "title": out.title,
            "state": out.state,
            "previous": out.previous,
            "note": notes.get(out.state, "Done.")
            if out.changed
            else f"It was already {out.state}; nothing changed.",
        }
        if out.url and out.changed and out.state == "active":
            link_carry.mint_document(out.url, published=False)
            result["url"] = out.url
        return result

    return document_set_status


async def _document_publish__p1(c):
    c.tool = "document_publish"
    if c.documents is None:
        _record(c.metrics, c.tool, "unavailable", reason="not_wired")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't publish documents right "
                    "now. Say so; do not invent a link and do not say one was made."
                ),
            },
        )
    c.link_carry.document_called()
    if c.spec is None or (isinstance(c.spec, str) and (not c.spec.strip())):
        _record(c.metrics, c.tool, "bad_arguments", reason="no_spec")
        return (
            True,
            None,
            {
                "status": "bad_arguments",
                "note": (
                    "No spec was sent, so nothing was "
                    "stored. The catalog is here: write the document against it and call again."
                ),
                "catalog": c.documents.catalog(),
            },
        )
    c.link_carry.document_tried()
    c.tagger = getattr(c.room, "tagger", None) if c.room is not None else None
    if c.document_actor not in c.document_asked or c.tagger is None:
        _record(c.metrics, c.tool, "unavailable", reason="no_requester")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "A document is published for the "
                    "person who asked, and nobody asked here, so nothing was stored."
                ),
            },
        )
    c.target: UUID | None = None
    if c.document_id is not None:
        c.target = c._document_id(c.document_id)
        if c.target is None:
            _record(c.metrics, c.tool, "bad_arguments", reason="bad_id")
            return (
                True,
                None,
                {
                    "status": "bad_arguments",
                    "note": (
                        "That is not a document id. "
                        "Use the `document_id` a publish or a read gave you."
                    ),
                },
            )
    try:
        c.out = await c.documents.publish(
            platform=c.room.platform,
            conversation_id=c.bound_conversation_id,
            person_id=c.tagger.person_id,
            raw=c.spec,
            person_names=c._route_person_names,
            live_addresses=frozenset(c._disclosed_live_addresses),
            written_by=(f"{c.document_actor}:{current_tag_id() or ''}"),
            document_id=c.target,
            base_revision=c.base_revision,
        )
    except Exception:
        log.exception("document_publish failed")
        _record(c.metrics, c.tool, "unavailable", reason="raised")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "The document could not be saved, so nothing was stored. Say you could not."
                ),
            },
        )
    c.rules = [f.rule for f in c.out.findings]
    if c.out.status == "ok":
        _record(c.metrics, c.tool, "ok", revision=c.out.revision)
        c.link_carry.document_saved()
        if not c.out.url:
            if c.subagent_job:
                return (
                    True,
                    None,
                    {
                        "status": "ok",
                        "document_id": str(c.out.document_id),
                        ("revision"): c.out.revision,
                        "note": "Stored, with no link yet. Name it in "
                        "your report by its title and this `document_id`: delivery reads it back.",
                    },
                )
            return (
                True,
                None,
                {
                    "status": "ok",
                    "document_id": str(c.out.document_id),
                    "revision": c.out.revision,
                    (
                        "note"
                    ): "Stored. You have no link to give yet. Say you made it and that it is "
                    "under Documents in the web app, found by its title, for people signed in. "
                    "Never write a path, a host or the `document_id` in a reply.",
                },
            )
        c.link_carry.mint_document(c.out.url, published=True)
        if c.subagent_job:
            return (
                True,
                None,
                {
                    "status": "ok",
                    "document_id": str(c.out.document_id),
                    "revision": c.out.revision,
                    ("url"): c.out.url,
                    "note": "Stored. Name it in your report by its title and this "
                    "`document_id`: delivery reads it back and adds the link. Do not write a link "
                    "yourself.",
                },
            )
        return (
            True,
            None,
            {
                "status": "ok",
                "document_id": str(c.out.document_id),
                "revision": c.out.revision,
                ("url"): c.out.url,
                "note": "Stored. The link to it is added to your reply for you: "
                "say it is saved and by what title, and for people signed in to the web app. "
                "Never write a link or the `document_id` yourself.",
            },
        )
    return (False, None, None)


async def _document_publish__p2(c):
    if c.out.status == "conflict":
        _record(c.metrics, c.tool, "bad_arguments", reason="conflict")
        return (
            True,
            None,
            {
                "status": "bad_arguments",
                "document_id": str(c.out.document_id),
                ("current_revision"): c.out.revision,
                "note": "The document changed since you read it, "
                "so nothing was stored. Read it again, apply your change to that, and publish "
                "again with its revision.",
            },
        )
    if c.out.status == "refused" and "document_not_active" in c.rules:
        _record(c.metrics, c.tool, "bad_arguments", reason="not_active")
        return (
            True,
            None,
            {
                "status": "bad_arguments",
                "note": (
                    "That document is archived or "
                    "deleted, so nothing was stored. Restore it with document_set_status (state "
                    "active) "
                    "first if the room wants it changed."
                ),
            },
        )
    if c.out.status == "refused" and "no_such_document" in c.rules:
        _record(c.metrics, c.tool, "not_found")
        return (
            True,
            None,
            {
                "status": "not_found",
                "note": (
                    "There is no document with that id in "
                    "this room. Nothing was stored. Publish without a `document_id` for a new one."
                ),
            },
        )
    if c.out.status == "refused" and "json" in c.rules:
        _record(c.metrics, c.tool, "bad_arguments", reason="not_json")
        return (
            True,
            None,
            {
                "status": "bad_arguments",
                "note": (
                    "Nothing was stored. The spec was "
                    "not valid JSON. Send `spec` as an object, not as text, fix the marked place, "
                    "and "
                    "call again. Until a call returns ok there is no document and no link: do not "
                    "tell anybody it was made."
                ),
                "findings": c._document_findings(c.out.findings),
            },
        )
    if c.out.status == "refused":
        _record(c.metrics, c.tool, "bad_arguments", reason="findings")
        return (
            True,
            None,
            {
                "status": "bad_arguments",
                "note": (
                    "Nothing was stored. Fix each "
                    "finding in the spec and call again; do not tell anybody it was published."
                ),
                "findings": c._document_findings(c.out.findings),
            },
        )
    _record(c.metrics, c.tool, "unavailable", reason=c.out.status)
    return (
        True,
        None,
        {
            "status": "unavailable",
            "note": (
                "The document could not be checked or "
                "saved, so nothing was stored. If it holds a Map, publish again with each place "
                "named in "
                "Markdown text instead; otherwise say it was not made."
            ),
        },
    )
    return (False, None, None)


def build(deps):
    document_publish = _make_document_publish(deps)
    document_read = _make_document_read(deps)
    document_set_status = _make_document_set_status(deps)
    return (
        _spec("document_publish", document_publish),
        _spec("document_read", document_read),
        _spec("document_set_status", document_set_status),
    )
