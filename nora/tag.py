"""Tag detection + per-group debounce."""

import time


def is_tagged(body: str, trigger: str) -> bool:
    return trigger.lower() in (body or "").lower()


class Debounce:
    """Ignore rapid re-tags in the same group. Rate limiting, not conversation state."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval = min_interval_seconds
        self._last: dict[str, float] = {}

    def allow(self, group_id: str) -> bool:
        now = time.monotonic()
        last = self._last.get(group_id, 0.0)
        if now - last < self.min_interval:
            return False
        self._last[group_id] = now
        return True
