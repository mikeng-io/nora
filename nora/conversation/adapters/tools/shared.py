from __future__ import annotations


def platform_message_id_of(raw: object) -> str | None:
    """The platform's own id for a stored row, out of its raw event.

    TWO spellings, because the platforms disagree and a quote is dropped
    SILENTLY when the wrong one is read. Discord and WhatsApp key it `id`;
    Instagram's bridge event keys it `message_id`. Reading only `id` would
    send every threaded Instagram reply UNTHREADED: `status: sent`, nothing
    logged, no visual thread in the app.

    A module-level function rather than a closure so the disagreement it
    encodes is testable on its own. Anything unparseable is None: a quote is
    an enhancement, and guessing an id would thread the reply onto the wrong
    message.
    """
    if not isinstance(raw, dict):
        return None
    for key in ("id", "message_id"):
        value = raw.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            text = str(value)
            if text:
                return text
    return None
