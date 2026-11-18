"""File routes use their application graph; managed routes use a request-local projection."""

from collections.abc import Mapping
from typing import Any, cast

from aiohttp import web


def data_context(request: web.Request) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], request.get("web_context", request.app))
