"""No em dash reaches the model: `DashFreeOpenAIModel`.

**Why this is a transport concern and not a doctrine one.** `voice.md` has
told her since v21 (2026-09-22) that the em dash is written-language and
marketing-copy punctuation, and her replies kept carrying it: 102 of her last
173 messages in the busiest room, 66 of the 110 sent AFTER the rule landed.
The rule was not the lever. Her input was: the tool schemas she reads every
turn carried 302 of them, and her own earlier messages in the transcript, the
most same-register specimen in front of her, carried a median of 14 more.

Measured 2026-09-24 by replaying 40 recorded tag turns against the deployed
`nora` alias, 2-3 samples each, one variable per arm:

    input                                         replies with an em dash
    as recorded                                   29/59   49%
    + her own past lines without them             12/60   20%
    + nothing she reads carries one               3/95     3%

So she copies it; she does not choose it. And the copying is self-sustaining:
a reply written with a dash becomes a transcript line that demonstrates the
dash to the next turn, which is why the rule never decayed into compliance.

**One place, because the sources are many and keep growing.** Tool
docstrings, block descriptions, her notes and peer cards (which models wrote),
fetched pages and her own history all feed the same request. Cleaning each
source is a diff per source and a regression per new one; normalising the
request is one function with one test
(`tests/unit/test_punctuation.py`). It changes punctuation only: a pause in a
Cantonese line becomes the full-width comma `voice.md` asks for, and in any
other text a comma. Nothing she reads loses a word.

It rewrites what the MODEL is sent, never what is stored or delivered: the
audit rows, the transcript table and every outbound message are untouched, so
provenance is exactly what it was.
"""

from __future__ import annotations

import re
from typing import Any

from strands.models.openai import OpenAIModel

# U+2014 EM DASH and U+2015 HORIZONTAL BAR (the 「――」 some IMEs produce),
# with any spacing around them. NOT U+2013: the en dash marks a range (2 to 5 days)
# and carries meaning.
_DASH = re.compile(r"[ \t]*[\u2014\u2015]+[ \t]*")


def _is_cjk(ch: str) -> bool:
    return ch >= "\u2e80"


def undash(text: str) -> str:
    """Every em dash becomes a comma: full-width when either side of it is
    Chinese («link——冇人接» is a Cantonese line), «, » otherwise."""
    if "\u2014" not in text and "\u2015" not in text:
        return text

    def repl(m: re.Match[str]) -> str:
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        cjk = any(ch and _is_cjk(ch) for ch in (before, after))
        return "\uff0c" if cjk else ", "

    return _DASH.sub(repl, text)


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
        return request
