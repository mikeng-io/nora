from __future__ import annotations

from typing import Literal

from nora.agent.toolkit.results import _record
from nora.domain.managed import OutcomeStatus
from nora.people.app.room_invocation import Clarify, RoomRequest, _guess_kind


def _make_member_update(deps):
    metrics = deps.metrics

    async def member_update(
        action: Literal["register", "add", "remove"],
        target: str | None = None,
        target_kind: Literal["mention", "account", "name"] | None = None,
    ) -> dict:
        """Manage members when someone asks.

        You cannot supply the actor, the room, or any credential: who asked and
        where are taken from the verified message itself. An unregistered
        account must be registered (`action="register"`) before membership can
        name the person. The action is authenticated as the actual sender of
        this message, never as whoever is named.

        Returns `status`: ok (`action` applied), already_present (the same ask
        already landed), refused (no authority: say who asked, and that they
        are not a room operator), ambiguous or nothing_found (the target did
        not resolve: ask), partial (the write only partly landed: warn, and
        do not call again), or unavailable (the identity store could not
        answer: say you cannot do it now), with a `note` for every status.

        Args:
            action: "register" a referenced unbound account as a new person;
                "add" or "remove" one person's room membership.
            target: Who or what the request names: omit for the account this
                message @-mentions, else the exact platform account id or a
                name this room knows.
            target_kind: "mention", "account" or "name". Leave it out and it
                is inferred from `target`: no target is the message's mention,
                an id-shaped value is "account", anything else is "name".
        """
        commands = deps.room_management
        message = deps.message
        if commands is None or message is None:
            _record(metrics, "member_update", "unavailable", reason="unwired")
            return {
                "status": "unavailable",
                "note": "Room membership management is not wired here. Say "
                "you cannot do that in this deployment.",
            }
        reference = (target or "").strip() or None
        kind: Literal["mention", "account", "name"]
        if target_kind is None:
            if reference is None:
                kind = "mention"
            elif _guess_kind(message.platform, reference) is not None:
                kind = "account"
            else:
                kind = "name"
        else:
            kind = target_kind
        result = await commands.invoke(
            message,
            RoomRequest(kind=action, reference=reference, reference_kind=kind),
        )
        if isinstance(result, Clarify):
            _record(metrics, "member_update", result.status)
            return {"status": result.status, "note": result.question}
        status = _OUTCOME_STATUS.get(result.status, "unavailable")
        if result.status is OutcomeStatus.COMPLETE and result.detail in (
            "replayed", "already-observed",
        ):
            status = "already_present"
        _record(
            metrics, "member_update", status,
            detail=result.detail or status,
        )
        return {
            "status": status,
            "note": (
                f"The result carried {result.status.value}"
                + (f" ({result.detail})" if result.detail else "")
                + ". " + commands.render(result)
            ),
        }

    return member_update


#: `OutcomeStatus` → the tool's declared status axis (`refused` for an
#: authoritative denial, `partial` for a journaled half-effect).
_OUTCOME_STATUS = {
    OutcomeStatus.COMPLETE: "ok",
    OutcomeStatus.DENIED: "refused",
    OutcomeStatus.UNAVAILABLE: "unavailable",
    OutcomeStatus.INCOMPLETE: "partial",
}
