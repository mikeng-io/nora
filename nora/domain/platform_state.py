"""The liveness vocabulary of a platform (docs/design/platform-bridges/platform-liveness.md).

`live` — its own identity is resolved and registered; inbound and outbound run.
`degraded` — enabled and built, but its identity could not be resolved; it
hears nothing, sends nothing and has no identity entry until it is promoted.
`disabled` — switched off in config.
"""

from __future__ import annotations

from typing import Literal, get_args

PlatformState = Literal["live", "degraded", "disabled"]
PLATFORM_STATES: tuple[str, ...] = get_args(PlatformState)
