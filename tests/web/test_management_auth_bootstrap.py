"""Authentication keys and provisioning authority remain separate at real startup."""

import pytest
from aiohttp import web

from nora.app.management import mount_management
from nora.app.startup import StartupError
from nora.config import load
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

KEY = "generated-private-authentication-key-for-local-contracts"
SETTINGS = '''
[identity]
backend = "managed"
[identity.managed]
kratos_admin_url = "http://127.0.0.1:4434"
keto_read_url = "http://127.0.0.1:4466"
keto_write_url = "http://127.0.0.1:4467"
[identity.authentication.generated_portal]
key_env = "NORA_TEST_AUTHENTICATION_KEY"
methods = {discord_oauth = "generated-client"}
'''


@pytest.fixture
def configured(example_config_path, monkeypatch):
    monkeypatch.delenv("NORA_TEST_AUTHENTICATION_KEY", raising=False)
    monkeypatch.delenv("NORA_TEST_OTP_KEY", raising=False)
    base = example_config_path.read_text()

    def configuration(*, settings=SETTINGS, key=KEY, shared=False):
        configured_base = base.replace(
            '[otp]\n', '[otp]\napi_token_env = "NORA_TEST_OTP_KEY"\n',
        ) if shared else base
        example_config_path.write_text(configured_base + settings)
        credentials = {"NORA_TEST_AUTHENTICATION_KEY": key}
        if shared:
            credentials["NORA_TEST_OTP_KEY"] = key
        private = example_config_path.parent / "secret.toml"
        private.write_text("[credentials]\n" + "\n".join(
            f'{name} = "{value}"' for name, value in credentials.items()
        ))
        return load(example_config_path, require_secrets=False)

    return configuration


async def test_authentication_mounts_without_a_provisioning_service(configured, aiohttp_client):
    app = web.Application()
    services = ManagedServices(FakeBindingStore(), FakeAuthorityBackend(), FakeTopologySource(),
                               FakeIdentityRegistry(), FakeLinkRequests())
    await mount_management(app, cfg=configured(), services=services)
    client = await aiohttp_client(app)
    response = await client.post("/app/v1/management/auth/exchange")
    assert response.status == 401
    assert (await client.post("/app/v1/management/operations")).status == 401


@pytest.mark.parametrize("key", ["", "too-short"])
async def test_unusable_authentication_keys_refuse_startup(configured, key):
    app = web.Application()
    with pytest.raises(StartupError, match="authentication issuer"):
        await mount_management(app, cfg=configured(key=key), services=None)
    assert list(app.router.routes()) == []


async def test_an_otp_service_bearer_cannot_become_an_authentication_key(configured):
    with pytest.raises(StartupError, match="authentication issuer"):
        await mount_management(web.Application(), cfg=configured(shared=True),
                               services=None)


async def test_file_backend_does_not_mount_authentication_or_require_its_key(
    configured, aiohttp_client,
):
    app = web.Application()
    cfg = configured(settings=SETTINGS.replace('backend = "managed"', 'backend = "file"'), key="")
    await mount_management(app, cfg=cfg, services=None)
    client = await aiohttp_client(app)
    assert (await client.post("/app/v1/management/auth/check")).status == 404
