"""No em dash reaches the model: `DashFreeOpenAIModel`.

**Why this is a transport concern and not a doctrine one.** `voice.md` tells
her the em dash is written-language and marketing-copy punctuation, and a
rule alone does not stop it: she copies what she reads. The tool schemas she
reads every turn, and her own earlier messages in the transcript (the most
same-register specimen in front of her), carry em dashes, and a reply written
with a dash becomes a transcript line that demonstrates the dash to the next
turn. Removing the dash from everything she reads breaks that loop; the rule
alone does not.

**One place, because the sources are many and keep growing.** Tool
docstrings, block descriptions, her notes and peer cards (which models wrote),
fetched pages and her own history all feed the same request. Cleaning each
source is a diff per source and a regression per new one; normalising the
request is one function with one test
(`tests/unit/test_punctuation.py`). It changes punctuation only: a pause in a
Cantonese line becomes the full-width comma `voice.md` asks for, and in any
other text a comma. Nothing she reads loses a word.

It rewrites what the MODEL is sent, never what is stored or delivered: the
audit rows, the transcript table and every outbound message are untouched.
"""

from __future__ import annotations

from typing import Any

from strands.models.openai import OpenAIModel

from nora.domain.prompt import undash as undash

from .replay_capture import client_capture
from .telemetry import record_eval_model_request


def _clean(value: Any) -> Any:
    """Every string in a request-shaped structure, recursively.

    Tool-call ARGUMENTS in assistant history are skipped: they are JSON the
    model wrote and the SDK replays verbatim, and a reply already sent is not
    a thing to re-punctuate. Only what she READS is cleaned.
    """
    if isinstance(value, str):
        return undash(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: (v if k in ("tool_calls", "arguments") else _clean(v))
                for k, v in value.items()}
    return value


class DashFreeOpenAIModel(OpenAIModel):
    """`OpenAIModel` whose request carries no em dash in any text it sends."""

    def format_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super().format_request(*args, **kwargs)
        for key in ("messages", "tools"):
            if key in request:
                request[key] = _clean(request[key])
        record_eval_model_request(request)
        return request

    def _get_client(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def captured():
            async with (
                super(DashFreeOpenAIModel, self)._get_client() as client,
                client_capture(client) as selected,
            ):
                yield selected
        return captured()
