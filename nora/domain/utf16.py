"""The unit every range Signal carries is measured in. One function, on purpose.

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
