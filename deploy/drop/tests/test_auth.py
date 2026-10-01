from __future__ import annotations

from starlette.requests import Request

from auth import bearer_ok


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_the_right_bearer_token_passes():
    assert bearer_ok(_request({"Authorization": "Bearer t"}), token="t")  # noqa: S106


def test_a_wrong_token_fails():
    assert not bearer_ok(
        _request({"Authorization": "Bearer wrong"}), token="t"  # noqa: S106
    )


def test_a_missing_header_fails():
    assert not bearer_ok(_request({}), token="t")  # noqa: S106


def test_a_non_bearer_scheme_fails():
    assert not bearer_ok(
        _request({"Authorization": "Basic dXNlcjpwYXNz"}), token="t"  # noqa: S106
    )
