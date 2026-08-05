"""Stage: admission — owns R-9 (debounce), R-10 (in-flight lock) and R-12
(self-tag refusal), the three rules that were inline in
`Handler.on_event` (`nora/handler.py:80-97`).

R-10 ownership split, spelled out because a lock acquired in one module and
released in another is exactly the shape that grows leaks: THIS module
ACQUIRES — `run()` adds `key` to `in_flight` on admission, and only on
admission. It is the PIPELINE's `finally` that RELEASES (discards `key` from
the same `in_flight` set on every exit path) — see `nora.engagement.
pipeline`'s docstring for the other half of this contract. Nothing in this
module ever discards from `in_flight`.

R-12 checks membership in `identity.aliases`, not equality against a single
id — see `nora.domain.ports`'s module docstring for why: signal-cli emits
`source` or `sourceNumber` and sometimes a UUID for the same account, and
Telegram has both a numeric id and a username, so equality against one form
would silently stop recognizing Nora's own messages the moment a second
platform (or a second id form on this one) is wired in. The caller
(`nora.engagement.pipeline.EngagementPipeline.run`) resolves the identity via
`adapter.resolve_identity()` — memoized after the adapter's first call, and
already forced to succeed once at startup by
`nora.platform.registry.AdapterRegistry.from_config`'s fail-fast — and passes
it in here already resolved, so this stage does no I/O of its own.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import PlatformIdentity
from nora.tag import Debounce

log = logging.getLogger("nora.engagement.admission")


async def run(
    *,
    message: NormalizedMessage,
    identity: PlatformIdentity,
    debounce: Debounce,
    in_flight: set[tuple[str, str]],
    metrics: Any,
) -> bool:
    """Return True if the tag is admitted.

    A True return means `key` has already been added to `in_flight` — the
    caller (the pipeline) owns releasing it; see the module docstring.
    """
    if message.sender_id in identity.aliases:               # R-12
        log.info("ignoring a trigger from our own account")
        return False

    key = (message.platform, message.conversation_id)
    if not debounce.allow(key):                             # R-9
        log.info("debounced tag in %s", message.conversation_id)
        metrics.inc("nora_tags_debounced_total")
        return False

    if key in in_flight:                                    # R-10: drop, do not queue
        log.info("tag_dropped_in_flight in %s", message.conversation_id)
        metrics.inc("nora_tags_dropped_total", reason="in_flight")
        return False

    in_flight.add(key)
    metrics.inc("nora_tags_total")
    return True
