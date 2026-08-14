"""Discord snowflake id -> creation timestamp.

2015-01-01T00:00:00Z is the origin every Discord snowflake id is offset from
(`discord.utils.DISCORD_EPOCH`). Reading it off the id directly, rather than
via discord.py's `created_at` property, avoids putting a timezone-aware
`datetime` on any of this package's narrow Protocols (`.parser._DiscordMessage`
and friends) — every caller here wants milliseconds, not a `datetime`.
"""

from __future__ import annotations

DISCORD_EPOCH_MS = 1420070400000


def timestamp_ms(snowflake_id: int) -> int:
    return (snowflake_id >> 22) + DISCORD_EPOCH_MS
