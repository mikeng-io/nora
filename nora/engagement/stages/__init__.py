"""Engagement stages: admission, invoke, deliver, feedback.
`deliver` has two public entry points (`build`/`check_outcome`) rather than
one `run` — see its module docstring for why. Orchestration lives in
`nora.engagement.pipeline`, not here.
"""

from __future__ import annotations
