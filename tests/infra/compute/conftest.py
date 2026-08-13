"""Compatibility shim for `aioresponses` 0.7.9 against `aiohttp` >=3.14.

`aiohttp` 3.14 added a required keyword-only `stream_writer` argument to
`aiohttp.ClientResponse.__init__` (only `stream_writer.output_size` is ever
read off it). `aioresponses` 0.7.9 -- its latest PyPI release at the time
this suite was written -- constructs `ClientResponse` without that argument,
so every mocked response raises `TypeError: ClientResponse.__init__()
missing 1 required keyword-only argument: 'stream_writer'`. The fix
(`aioresponses` PR #288, https://github.com/pnuckowski/aioresponses/pull/288)
is open but unreleased as of this writing.

This shim defaults `stream_writer` to a `Mock(output_size=0)` when the
caller (aioresponses) omits it, mirroring that PR's own approach, and only
for the duration of tests in this directory -- `monkeypatch` restores the
real `__init__` afterwards. Delete this file once a released `aioresponses`
carries the upstream fix and `uv add --dev "aioresponses>=<fixed-version>"`
picks it up."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest

_original_init = aiohttp.ClientResponse.__init__


@pytest.fixture(autouse=True)
def _aioresponses_stream_writer_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    def patched_init(self: aiohttp.ClientResponse, method: str, url: Any, **kwargs: Any) -> None:
        kwargs.setdefault("stream_writer", Mock(output_size=0))
        _original_init(self, method, url, **kwargs)

    monkeypatch.setattr(aiohttp.ClientResponse, "__init__", patched_init)
