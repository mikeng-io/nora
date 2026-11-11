from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from nora.agent.subagent_desk import JobView
from nora.agent.toolkit.results import _record
from nora.agent.toolkit.spec import _spec
from nora.domain.subagent import Decision, SubagentBrief
from nora.obs.logging import current_tag_id

log = logging.getLogger("nora.tools")


def _make__subagent_unavailable(deps):
    metrics = deps.metrics

    def _subagent_unavailable(tool: str, reason: str) -> dict:
        _record(metrics, tool, "unavailable", reason=reason)
        return {
            "status": "unavailable",
            "note": "I can't reach background jobs right now. Say so; nothing was "
            "started or changed.",
        }

    return _subagent_unavailable


def _make__subagent_view(deps):
    def _subagent_view(view: JobView) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": view.id,
            "title": view.title,
            "state": view.state,
            "description": view.description,
            "age_seconds": view.age_seconds,
        }
        if view.rounds is not None:
            out["rounds"] = view.rounds
        if view.last_tool:
            out["last_tool"] = view.last_tool
        if view.report:
            out["report"] = view.report
        if view.document_ids:
            titles = list(view.document_titles) + [""] * len(view.document_ids)
            out["documents"] = [
                {"document_id": i, "title": titles[n]} for n, i in enumerate(view.document_ids)
            ]
        if view.failure:
            out["failure"] = view.failure
        if view.checkpoint:
            out["checkpoint"] = view.checkpoint
        return out

    return _subagent_view


def _make_subagent_create(deps):
    conversation_type = deps.conversation_type
    metrics = deps.metrics
    room = deps.room
    subagent = deps.subagent
    bound_conversation_id = deps.bound_conversation_id
    subagent_on_tag = deps.subagent_on_tag

    async def subagent_create(
        title: str,
        objective: str,
        deliverable: str,
        decisions: list[dict[str, str]] | None = None,
        inputs: list[str] | None = None,
        document_ids: list[str] | None = None,
    ) -> dict:
        """Hand a long job to a background worker that reports later. Call this when
        work needs many files or documents, or research then writing; not for what one
        reply answers.

        MUST: give it a short `title` the room would recognise, and write the brief for a
        reader who never saw the chat: the `objective`, what done looks like in
        `deliverable`, and everything the room settled in `decisions`.

        MUST: after `started`, tell the room you are on it and that the result comes
        later. Do not wait for it or poll.

        `status` is one of:
        - `started`: the job is running; it carries `id`.
        - `bad_arguments`: nothing started; the note says what to fix.
        - `unavailable`: nothing started.
        Every result carries a `note` saying how to report it.

        Args:
            title: A name for the job the room would recognise, at most 60 characters.
            objective: The job, in your own words, complete on its own.
            deliverable: What done looks like: what exists, and where.
            decisions: What was settled with the room, each {question, answer, by}; `by`
                is who decided, or `nora` for an assumption you made.
            inputs: Paths in this unit's shared computer workspace the job works from.
            document_ids: Documents to revise, from your documents view or a publish.
        """
        tool = "subagent_create"
        if subagent is None:
            _record(metrics, tool, "unavailable", reason="not_wired")
            return {
                "status": "unavailable",
                "note": "I can't start background jobs right now. Say so; nothing was started.",
            }
        tagger = getattr(room, "tagger", None) if room is not None else None
        if not subagent_on_tag or tagger is None:
            _record(metrics, tool, "unavailable", reason="no_requester")
            return {
                "status": "unavailable",
                "note": "A background job is started for the person who asked, in a "
                "turn they started. Nothing was started.",
            }
        if isinstance(inputs, str) or isinstance(document_ids, str):
            _record(metrics, tool, "bad_arguments", reason="not_a_list")
            return {
                "status": "bad_arguments",
                "note": "`inputs` and `document_ids` are lists of strings, not one string. "
                "Nothing was started; call again with lists.",
            }
        listed = decisions or []
        if not all(isinstance(d, Mapping) for d in listed):
            _record(metrics, tool, "bad_arguments", reason="decisions")
            return {
                "status": "bad_arguments",
                "note": "Each decision is an object with `question`, `answer` and `by`. "
                "Nothing was started; call again with them fixed.",
            }
        brief = SubagentBrief(
            title=str(title or ""),
            objective=str(objective or ""),
            deliverable=str(deliverable or ""),
            decisions=tuple(
                Decision(str(d.get("question", "")), str(d.get("answer", "")), str(d.get("by", "")))
                for d in listed
            ),
            inputs=tuple(str(p) for p in inputs or ()),
            document_ids=tuple(str(i) for i in document_ids or ()),
        )
        out = await subagent.create(
            platform=room.platform if room is not None else "",
            conversation_id=bound_conversation_id,
            conversation_type=conversation_type,
            requester_person_id=tagger.person_id,
            tag_id=current_tag_id() or None,
            brief=brief,
        )
        if out.status == "bad_arguments":
            _record(metrics, tool, "bad_arguments", reason="brief")
            return {
                "status": "bad_arguments",
                "note": f"Nothing was started: {out.problem}. Call again with it fixed.",
            }
        if out.status != "started":
            _record(metrics, tool, "unavailable", reason=out.reason or "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't start it, and nothing is running. Say so; don't "
                "say you are on it.",
            }
        if out.repeated:
            _record(metrics, tool, "started", reason="repeated_in_turn")
            return {
                "status": "started",
                "id": out.id,
                "note": "This job was already started earlier in this turn, and nothing "
                "new was started. Tell the room once that you are on it.",
            }
        _record(metrics, tool, "started")
        return {
            "status": "started",
            "id": out.id,
            "note": "It is running in the background. Tell the room you are on it and "
            "that the result comes later, as a message. Do not wait for it, "
            "and do not say when it will be done.",
        }

    return subagent_create


def _make_subagent_list(deps):
    metrics = deps.metrics
    room = deps.room
    subagent = deps.subagent
    _subagent_unavailable = _make__subagent_unavailable(deps)
    _subagent_view = _make__subagent_view(deps)
    bound_conversation_id = deps.bound_conversation_id
    subagent_on_tag = deps.subagent_on_tag

    async def subagent_list() -> dict:
        """List this room's background jobs, running and recent, with state and age.
        Call this when somebody asks what you are working on, or to find a job's id.

        Returns `status`: ok, nothing_found or unavailable, with `jobs`.
        """
        tool = "subagent_list"
        if subagent is None or not subagent_on_tag:
            return _subagent_unavailable(tool, "not_wired")
        # The person asking: the documents of each job are listed to them, never to the requester.
        tagger = getattr(room, "tagger", None) if room is not None else None
        try:
            views = await subagent.recent(
                platform=room.platform if room is not None else "",
                conversation_id=bound_conversation_id,
                person_id=tagger.person_id if tagger is not None else None,
            )
        except Exception:
            log.exception("subagent_list failed")
            return _subagent_unavailable(tool, "raised")
        if not views:
            _record(metrics, tool, "nothing_found")
            return {"status": "nothing_found", "note": "There are no background jobs in this room."}
        _record(metrics, tool, "ok", count=len(views))
        return {
            "status": "ok",
            "jobs": [_subagent_view(v) for v in views],
            "note": "Newest first. Use `subagent_read` with an `id` for one job.",
        }

    return subagent_list


def _make_subagent_read(deps):
    metrics = deps.metrics
    room = deps.room
    subagent = deps.subagent
    _subagent_unavailable = _make__subagent_unavailable(deps)
    _subagent_view = _make__subagent_view(deps)
    bound_conversation_id = deps.bound_conversation_id
    subagent_on_tag = deps.subagent_on_tag
    subagent_read_notes = deps.subagent_read_notes

    async def subagent_read(id: str) -> dict:
        """Read one background job of this room: its state, or its report once it ended.
        Call this when somebody asks how a job is going.

        While it runs you get its age, rounds used and last tool; after, its report and
        documents.

        Returns `status`: ok, nothing_found or unavailable, with a `note`.

        Args:
            id: The job's `id`, from `subagent_create` or `subagent_list`.
        """
        tool = "subagent_read"
        if subagent is None or not subagent_on_tag:
            return _subagent_unavailable(tool, "not_wired")
        # The person asking: the documents of the job are shown to them, never to the requester.
        tagger = getattr(room, "tagger", None) if room is not None else None
        try:
            view = await subagent.read(
                platform=room.platform if room is not None else "",
                conversation_id=bound_conversation_id,
                job_id=id,
                person_id=tagger.person_id if tagger is not None else None,
            )
        except Exception:
            log.exception("subagent_read failed")
            return _subagent_unavailable(tool, "raised")
        if view is None:
            _record(metrics, tool, "nothing_found")
            return {
                "status": "nothing_found",
                "note": "There is no background job with that id in this room.",
            }
        _record(metrics, tool, "ok", state=view.state)
        return {
            "status": "ok",
            **_subagent_view(view),
            "note": subagent_read_notes.get(view.state, "Report it as it is."),
        }

    return subagent_read


def _make_subagent_cancel(deps):
    metrics = deps.metrics
    room = deps.room
    subagent = deps.subagent
    _subagent_unavailable = _make__subagent_unavailable(deps)
    bound_conversation_id = deps.bound_conversation_id
    subagent_on_tag = deps.subagent_on_tag

    async def subagent_cancel(id: str) -> dict:
        """Stop one of this room's background jobs. Call this when somebody asks you to
        stop or drop a job.

        Returns `status`: ok, already_present (nothing left to stop), nothing_found
        or unavailable, with a `note`.

        Args:
            id: The job's `id`, from `subagent_create` or `subagent_list`.
        """
        tool = "subagent_cancel"
        if subagent is None or not subagent_on_tag:
            return _subagent_unavailable(tool, "not_wired")
        try:
            done = await subagent.cancel(
                platform=room.platform if room is not None else "",
                conversation_id=bound_conversation_id,
                job_id=id,
            )
        except Exception:
            log.exception("subagent_cancel failed")
            return _subagent_unavailable(tool, "raised")
        if done is None:
            _record(metrics, tool, "nothing_found")
            return {
                "status": "nothing_found",
                "note": "There is no background job with that id in this room.",
            }
        view, changed = done
        if not changed:
            _record(metrics, tool, "already_present", state=view.state)
            return {
                "status": "already_present",
                "id": view.id,
                "state": view.state,
                "note": "Nothing was left to stop: it was already " + view.state + ". Say so.",
            }
        _record(metrics, tool, "ok")
        return {
            "status": "ok",
            "id": view.id,
            "state": view.state,
            "note": "It is cancelled and will not report. Say so.",
        }

    return subagent_cancel


def build(deps):
    subagent_create = _make_subagent_create(deps)
    subagent_list = _make_subagent_list(deps)
    subagent_read = _make_subagent_read(deps)
    subagent_cancel = _make_subagent_cancel(deps)
    return (
        _spec("subagent_create", subagent_create),
        _spec("subagent_list", subagent_list),
        _spec("subagent_read", subagent_read),
        _spec("subagent_cancel", subagent_cancel),
    )
