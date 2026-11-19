from __future__ import annotations

from urllib.parse import unquote

from nora.agent.security import looks_like_coordinate, scan_identifiers
from nora.agent.toolkit.results import _record
from nora.computer.adapters.tools.sessions import _browse_context, _computer_browse__prepare
from nora.computer.adapters.tools.shared import _clip
from nora.infra.computer.browser_retrieval import http_url


def _make_web_browse(deps):
    retrieve = _make_retrieve(deps)

    async def web_browse(
        objective: str, start_url: str | None = None, task: str | None = None,
    ) -> dict:
        """Read interactive public pages. Call this when a link needs browser
        interaction to answer a question. Returns an answer and its source.

        Returns `status`:
        - ok: evidence answers all or part of the question.
        - nothing_found: the pages read did not show the requested fact.
        - queued: the browser task is waiting its turn.
        - unavailable: the browser could not complete the check; `answer`, when
          present, is only what it read before stopping.

        Args:
            objective: The public facts to check on the page.
            start_url: The initial public HTTP or HTTPS link, if opening a tab.
            task: The task handle to continue on its own tab.
        """
        scope = deps.computer_ctx.bound_computer_scope
        if not (scope and deps.computer_admission and deps.browser_manager and deps.web_browser):
            _record(deps.metrics, "web_browse", "unavailable")
            return {
                "status": "unavailable",
                "note": "An owned browser is unavailable for this conversation.",
            }
        if (
            not objective.strip() or not (start_url or task)
            or (start_url and not http_url(start_url))
            or any(
                scan_identifiers(value, content_checks=deps.content_checks) is not None
                or (deps.content_checks and looks_like_coordinate(value))
                for value in (objective, unquote(start_url or ""))
            )
        ):
            _record(deps.metrics, "web_browse", "unavailable")
            return {
                "status": "unavailable",
                "note": "Use a public web link and leave out personal details.",
            }
        result = await retrieve(objective=objective, start_url=start_url, task=task)
        _record(deps.metrics, "web_browse", result["status"])
        return {
            "status": result["status"],
            "note": result.get("note") or "Report only the evidence the browser verified.",
            "answer": result.get("answer"),
            "url": result.get("url"),
            "task": result.get("task"),
            "tab": result.get("tab"),
            "reason": result.get("reason"),
            "uncertain": result.get("uncertain", False),
        }

    return web_browse



def _make_retrieve(deps):
    @deps.computer_ctx._queued_browse
    async def web_browse(objective: str, start_url: str | None, task: str | None) -> dict:
        context = _browse_context(deps, objective, start_url, task, deps.web_browser, "web_browse")
        _, _, result = await _computer_browse__prepare(context)
        if result is None:
            got = context.got
            if got.answer:
                # A run cut at its limit still owns its tab and holds what it read.
                text, _ = _clip(got.answer, context.computer_bounds.max_output_chars)
                return {
                    "status": "unavailable", "reason": got.reason, "answer": text,
                    "url": got.url, "task": got.task_id, "tab": got.tab,
                    "note": "Stopped before every page was read. The answer is what it read; "
                    "the rest is unconfirmed. Pass `task` to continue.",
                }
            return {
                "status": "unavailable", "reason": got.reason,
                "uncertain": bool(got.uncertain),
                "note": "The browser could not verify an answer. Keep missing facts unconfirmed.",
            }
        return result

    return web_browse
