"""WHICH LOOP IS SPEAKING — one declaration, read by everything that labels a
series or a row with it.

`audience` has been a label on `nora_silent_failures_total`,
`nora_llm_latency_seconds` and `nora_llm_stop_reason_total` since design spec
§6.2, and it is a REQUIRED keyword on `deliver.build` with no default for a
measured reason: an omitted one records `audience=""` (`Metrics._full_labels`),
which the A8 runbook filter then excludes in silence.

**The names lived in two places and one of them was a bare literal.**
`nora/app/fire.py` declared four of them as module constants;
`nora/engagement/pipeline.py` passed `"tag"` inline. Nothing named the SET, so
`reasoning_strip_log`'s `path` column shipped documented as
«tag / participation / dreamer:<adaptor>» while `heartbeat` and `proactive`
were reaching it from `fire.py` — and an operator filtering
`WHERE path IN ('tag','participation')` off that documentation would have
dropped the reminder path without noticing (audit, 2026-09-17). The fix is not
a longer comment; it is one constant the code passes and the help text reads
from, which is the repo's own label-vocabulary rule.

**`SPEAKING` vs `SHADOW` is the distinction that matters to a reader of
`reasoning_strip_log`.** The four speaking audiences run a model loop and can
therefore produce a row. The two shadow ones go through `deliver.build` — they
ARE sends, through every guard — but they never invoke an agent, so their watch
is built and stays inert. A `path` value from that pair appearing in the table
would mean the mirror had grown a model call.
"""

from __future__ import annotations

from typing import Literal, get_args

#: The tag path: somebody addressed her (`nora.engagement.pipeline`).
TAG = "tag"
#: A reminder or gathering coming due (`nora.app.fire.FirePath`, its default).
HEARTBEAT = "heartbeat"
#: The ambient participation occasion (design/dreamer-agent-loop/35).
PARTICIPATION = "participation"
#: A `kind="proactive"` reminder Nora wrote herself (39 D4/D12, DEC-162).
#: NOT the same loop as the Dreamer's `proactive` ADAPTOR, which reaches
#: `reasoning_strip_log` as `dreamer:proactive`: this one is a turn that may
#: speak into a room, that one is a background research pass that cannot.
#: The word is shared and the loops are not.
PROACTIVE = "proactive"

SpeakingAudience = Literal["tag", "heartbeat", "participation", "proactive"]

#: Every audience that runs a model loop, and therefore every `path` value
#: `reasoning_strip_log` can carry that is not a Dreamer adaptor. DERIVED from
#: the `Literal` above so the help text and the vocabulary cannot disagree.
SPEAKING_AUDIENCES: tuple[str, ...] = get_args(SpeakingAudience)

#: The §10.6 participation shadow mirror: operator review traffic, never room
#: speech, so its failures must not read as the tag path's.
SHADOW = "participation-shadow"
#: The relevance gate's shadow channel, same posture.
GATE_SHADOW = "relevance-gate-shadow"

#: Audiences that pass through `deliver.build` but never invoke an agent. Their
#: `ReasoningStripWatch` is constructed and never fires — named here so that a
#: row carrying one is legible as «the mirror grew a model call» rather than as
#: an unknown string.
SHADOW_AUDIENCES: tuple[str, ...] = (SHADOW, GATE_SHADOW)

AUDIENCES: tuple[str, ...] = SPEAKING_AUDIENCES + SHADOW_AUDIENCES
