"""WHICH LOOP IS SPEAKING — one declaration, read by everything that labels a
series or a row with it.

`audience` is a label on `nora_silent_failures_total`,
`nora_llm_latency_seconds` and `nora_llm_stop_reason_total`, and it is a
REQUIRED keyword on `deliver.build` with no default: an omitted one records
`audience=""` (`Metrics._full_labels`), which runbook filters then exclude in
silence.

The set of names is declared once here; the code passes these constants and
the `reasoning_strip_log` `path` help text reads from them, so a filter such
as `WHERE path IN (...)` is written against the real vocabulary.

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
#: The ambient participation occasion.
PARTICIPATION = "participation"
#: A `kind="proactive"` reminder Nora wrote herself (DEC-162).
#: NOT the same loop as the Dreamer's `proactive` ADAPTOR, which reaches
#: `reasoning_strip_log` as `dreamer:proactive`: this one is a turn that may
#: speak into a room, that one is a background research pass that cannot.
#: The word is shared and the loops are not.
PROACTIVE = "proactive"
#: A public summons: tagged under an Instagram post, answered in the comments
#: (`nora.app.summons`). It runs a model loop, so
#: it is SPEAKING, but its deliverer never reaches a room adapter: the reply is
#: held and published once by the summons' own flush.
SUMMONS = "summons"

SpeakingAudience = Literal["tag", "heartbeat", "participation", "proactive", "summons"]

#: Every audience that runs a model loop, and therefore every `path` value
#: `reasoning_strip_log` can carry that is not a Dreamer adaptor. DERIVED from
#: the `Literal` above so the help text and the vocabulary cannot disagree.
SPEAKING_AUDIENCES: tuple[str, ...] = get_args(SpeakingAudience)

#: The participation shadow mirror: operator review traffic, never room
#: speech, so its failures must not read as the tag path's.
SHADOW = "participation-shadow"
#: The relevance gate's shadow channel, same posture.
GATE_SHADOW = "relevance-gate-shadow"

#: Audiences that pass through `deliver.build` but never invoke an agent. Their
#: `ReasoningStripWatch` is constructed and never fires — named here so that a
#: row carrying one is legible as «the mirror grew a model call» rather than as
#: an unknown string.
SHADOW_AUDIENCES: tuple[str, ...] = (SHADOW, GATE_SHADOW)

#: A one-time code sent to one person (`nora.app.otp`): a fixed template, no
#: model, into a direct message. It goes through `deliver.build` so every guard
#: and the audit-first write apply, and it never invokes an agent.
ONE_TIME_CODE = "one-time-code"

#: Audiences that send a fixed notice rather than speech: through
#: `deliver.build`, never a model loop, and not review traffic either.
NOTICE_AUDIENCES: tuple[str, ...] = (ONE_TIME_CODE,)

AUDIENCES: tuple[str, ...] = SPEAKING_AUDIENCES + SHADOW_AUDIENCES + NOTICE_AUDIENCES
