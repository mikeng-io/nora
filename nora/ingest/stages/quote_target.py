"""A reply's quote, pointed at the message the transcript actually holds.

Signal quotes an edited message by the timestamp of the REVISION the replier
saw. The transcript keeps an edited message at its ORIGINAL timestamp — that is
the fold — so a reply to it carries a `quoted_ts` no row has, and would resolve
to nothing: no `replying_to`, no parent. This
stage rewrites such a pointer to the edited message's own `ts`, once, before
the audit write, so every reader downstream resolves it as it always has.

An exact match on a message always wins, so a quote that already points at a
row is never moved. A failed lookup leaves the quote as it was: a reply that
renders unthreaded, never a dropped message.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import RecordStore

log = logging.getLogger("nora.ingest.quote_target")


async def run(*, message: NormalizedMessage, audit: RecordStore, metrics: Any) -> NormalizedMessage:
    quote = message.quote
    # SIGNAL ONLY. It is the one network that quotes a revision: WhatsApp,
    # Instagram and Discord quote the parent's own timestamp, and running the
    # lookup there could only move a reply whose parent Nora never recorded
    # onto an unrelated message whose edit shares that millisecond.
    if message.platform != "signal" or quote is None or not quote.quoted_ts:
        return message
    try:
        ts = await audit.quoted_ts_of(
            conversation_id=message.conversation_id, ts=quote.quoted_ts,
        )
    except Exception:
        log.exception("quote target lookup failed; keeping the quote as sent")
        metrics.inc("nora_errors_total", stage="quote_target", platform=message.platform)
        return message
    if ts == quote.quoted_ts:
        return message
    return dataclasses.replace(message, quote=dataclasses.replace(quote, quoted_ts=ts))
