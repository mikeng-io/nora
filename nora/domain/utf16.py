"""The unit every range Signal (and now Discord) carries is measured in.

A Signal `bodyRange` — an @-mention (DEC-86) or a text style (DEC-82) — is
`(start, length)` **in UTF-16 code units**, and signal-cli says so twice in its
own man page, once under `--mention` and once under `--text-style`: "Unit of
start and length is UTF-16 code units, NOT Unicode code points." (Read back off
the shipped 0.14.7 binary on 2026-08-10, not from memory.)

WHY THIS IS ITS OWN MODULE
--------------------------
It lived in `nora.agent.outbound` while mentions were the only range in the
codebase. They are not any more: `nora.agent.markdown` turns `**bold**` into a
style range, and it runs BEFORE the composer rather than inside it (the text
has to exist in final form before the guards measure it — see
`nora.agent.guards.Deliverer.deliver`). Two producers of ranges, one arithmetic.

The alternative was for `markdown` to import it from `outbound`, which closes
an import cycle (`outbound` reads `egress.scan_identifiers`, `egress` reads
`markdown.strip_markdown`) — and the alternative to THAT is a second copy of
four characters of arithmetic, which is exactly the thing that must not exist.
A hand-rolled surrogate count in one of two places, drifting from the other, is
the failure this module's existence prevents.

TWO FUNCTIONS, ONE ARITHMETIC (added #211)
-------------------------------------------
`utf16_len` is the FORWARD direction — a Python string to a UTF-16 unit count —
and it is all Signal ever needed, because signal-cli's own JSON-RPC takes the
range as `"start:length:…"` and does the UTF-16 accounting on its own side.
Discord has no such out-of-band range: `nora.platform.discord.send` has to
splice `<@id>` directly into `content`, at the exact span `Mention.start`/
`.length` names — so it needs the REVERSE direction, a UTF-16 offset back to
the Python string index it names. `utf16_index` is that function, kept here
rather than beside the splice in `nora.platform.discord`, for the same reason
`utf16_len` is not beside `nora.agent.outbound`: one arithmetic, wherever it is
read from.
"""

from __future__ import annotations


def utf16_len(text: str) -> int:
    """Length of `text` in UTF-16 code units — the unit Signal's ranges use.

    NOT `len(text)`, and the difference is not theoretical in this group: 「幾」
    is one Python character and one UTF-16 unit, 「🇭🇰」 is two Python
    characters and FOUR UTF-16 units (two surrogate pairs), and 「😅」 — which
    is in the never-silent fallback line this repo already ships — is one
    Python character and TWO. Getting it wrong shifts every range after the
    first non-BMP character, and Signal does not reject the result: it renders
    the wrong span, in a real group, with nothing failing anywhere.

    Computed by encoding rather than by counting `ord(c) > 0xFFFF`, because the
    encoder is the definition and a hand-rolled surrogate count is a second
    implementation of it that can drift. `utf-16-le` (not `utf-16`) because the
    plain codec prepends a two-byte BOM, which would add one unit to every
    answer.
    """
    return len(text.encode("utf-16-le")) // 2


def utf16_index(text: str, offset: int) -> int:
    """The Python string index that `offset` UTF-16 code units into `text` names.

    The reverse of `utf16_len`, needed the moment a range computed in UTF-16
    units has to be SLICED out of a Python `str` — which indexes by code point
    — rather than handed to a JSON-RPC call that does its own UTF-16
    accounting. `nora.platform.discord.send` is the first caller: a `Mention`
    names a span to splice `<@id>` over, and `text[mention.start:...]` would be
    silently wrong the instant a non-BMP character (an emoji) appears before
    it, for the identical reason `len(text)` is the wrong thing to compute the
    offset WITH in the first place (`utf16_len`'s own docstring).

    Walks `text` one Python character at a time, counting 2 units for a
    non-BMP code point and 1 otherwise — `utf16_len`'s own definition of a
    "unit", applied incrementally rather than re-derived — until the running
    count reaches `offset`. A per-call walk rather than a cached table: a
    single message's mention count is small, so this costs nothing a real send
    would notice, and there is no second implementation of "what is a unit"
    left to drift from `utf16_len`.

    `offset` landing strictly BETWEEN the two units of one surrogate pair
    cannot happen for an offset this module's own callers produce: every
    `Mention`/`TextStyle` range is computed by `utf16_len` over a PREFIX of the
    same text (`nora.agent.outbound._mentions`, `nora.agent.markdown`), so it
    always lands on a real character boundary. An offset past the end of
    `text` (which a malformed range could still name) degrades to
    `len(text)` rather than raising — the caller is mid-send, and an
    `IndexError` here must not be how a decoration takes the message down.
    """
    units = 0
    for index, ch in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)
