from __future__ import annotations

from starlette.requests import Request

from auth import TokenScope, bearer_ok, mint_job_token, verify_job_token

SECRET = "test-job-secret"  # noqa: S105


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


def test_a_freshly_minted_create_token_verifies():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)
    assert verify_job_token(token=token, secret=SECRET, required_scope=TokenScope.CREATE)


def test_an_update_token_only_verifies_for_its_own_artifact_id():
    token = mint_job_token(
        secret=SECRET, scope=TokenScope.UPDATE, artifact_id="art_1", ttl_seconds=60,
    )
    assert verify_job_token(
        token=token, secret=SECRET, required_scope=TokenScope.UPDATE, artifact_id="art_1",
    )
    assert not verify_job_token(
        token=token, secret=SECRET, required_scope=TokenScope.UPDATE, artifact_id="art_2",
    )


def test_a_create_token_does_not_verify_as_an_update_token():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)
    assert not verify_job_token(
        token=token, secret=SECRET, required_scope=TokenScope.UPDATE, artifact_id="art_1",
    )


def test_an_update_token_does_not_verify_as_a_create_token():
    token = mint_job_token(
        secret=SECRET, scope=TokenScope.UPDATE, artifact_id="art_1", ttl_seconds=60,
    )
    assert not verify_job_token(token=token, secret=SECRET, required_scope=TokenScope.CREATE)


def test_an_expired_token_does_not_verify():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=-1)
    assert not verify_job_token(token=token, secret=SECRET, required_scope=TokenScope.CREATE)


def test_a_token_signed_with_the_wrong_secret_does_not_verify():
    token = mint_job_token(secret=SECRET, scope=TokenScope.CREATE, artifact_id=None, ttl_seconds=60)
    assert not verify_job_token(
        token=token, secret="wrong-secret", required_scope=TokenScope.CREATE,  # noqa: S106
    )
