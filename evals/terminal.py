"""Text an eval prints to a terminal, made safe to print.

A case's rendered input, a judge's reason and a draw's position are written by
a model or by a room. Printed raw, an escape in one of them moves the cursor,
clears the screen or retitles the window of whoever reads the output, so every
such line passes through `terminal_safe` first. Control characters are shown as
`\\xNN`, never interpreted; newlines and tabs stay, so the layout survives.
"""

from __future__ import annotations

import re
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def terminal_safe(text: str) -> str:
    """`text` with every control character (including ESC and the C1 range,
    where a single-byte CSI lives) shown as a `\\xNN` escape."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group(0)):02x}", text)


def safe_print(*values: object, **kwargs: Any) -> None:
    """Sanitize each value at the terminal boundary, including error paths."""
    print(*(terminal_safe(str(value)) for value in values), **kwargs)
