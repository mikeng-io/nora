"""Synthetic bodies for tests of block placement and rendering."""

import sys

from nora.domain.composed import Composed, compose_body


def compose_fixture(text: str) -> Composed:
    if not str(sys._getframe(1).f_globals["__name__"]).startswith("tests."):
        raise TypeError("synthetic context bodies are test-only")
    return compose_body(text)
