"""Engagement stages (spec §4.4): admission today; invoke/deliver/feedback are
later tasks. Each module has exactly one public `async def run(...)` —
orchestration lives in `nora.engagement.pipeline` (a later task), not here.
"""

from __future__ import annotations
