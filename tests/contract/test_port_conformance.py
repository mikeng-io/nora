"""Conformance: the concrete classes must satisfy the ports they are injected as.

This is what makes the ports real rather than aspirational. Where a class does NOT
conform, the gap is asserted explicitly, so the divergence is tracked, not
forgotten.

`PlatformAdapter` has no known-gap pin: `SignalAdapter` matches it on
`resolve_identity()`, `send()`, `health()`, `listen()` and the `platform`
attribute. `test_signal_adapter_satisfies_platform_adapter` below is the real
acceptance test — the `isinstance` call must return `True`.
"""

import inspect
from pathlib import Path

from nora.domain.ports import BlobStore, MediaSource, MemoryStore, PlatformAdapter
from nora.infra.blobs import FilesystemBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.honcho import HonchoClient
from nora.obs.metrics import Metrics
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.media import SignalMediaSource
from nora.platform.whatsapp.adapter import WhatsAppAdapter
from nora.testing import FakeBlobStore, FakeMediaSource, FakeMemoryStore


def test_honcho_client_satisfies_memory_store_method_names():
    for method in ("ensure_workspace", "ensure_peer", "feed", "peer_card",
                   "representation", "health", "aclose"):
        assert callable(getattr(HonchoClient, method, None)), f"HonchoClient lacks {method}"


def test_fake_memory_store_satisfies_memory_store():
    """`FakeMemoryStore` (`nora/testing/fakes.py`) is `MemoryStore`'s second
    implementation — `tests/integration/harness.py` and `evals/harness.py` both
    build their pipeline against it instead of the real, networked `HonchoClient`.
    `isinstance` against a `runtime_checkable` `Protocol` checks method names
    only (see `test_runtime_checkable_protocols_only_check_method_names` below);
    unlike `HonchoClient`, `FakeMemoryStore` is also keyword-only exactly like the
    port. It is the suite's only memory fake (R-C)."""
    assert isinstance(FakeMemoryStore(), MemoryStore)


def test_signal_adapter_resolve_identity_satisfies_the_port_shape():
    """Replaces `test_signal_adapter_gap_to_platform_adapter_is_known`, which pinned
    `resolve_identity()` as absent — the platforms phase adds it, so this is the
    positive assertion in its place. `send()`'s SendResult/keyword-only shape and
    `health()`'s AdapterHealth shape are asserted by their own tests, added
    alongside the tasks that close those two gaps."""
    assert hasattr(SignalAdapter, "resolve_identity")
    identity_sig = inspect.signature(SignalAdapter.resolve_identity)
    assert identity_sig.return_annotation == "PlatformIdentity"


def test_signal_adapter_send_satisfies_the_port_shape():
    """`send()` becomes keyword-only-after-self and returns `SendResult` instead
    of a positional-target bare `bool` — the platforms phase's second gap close."""
    send_params = inspect.signature(SignalAdapter.send).parameters
    assert send_params["target"].kind is inspect.Parameter.KEYWORD_ONLY
    assert send_params["text"].kind is inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(SignalAdapter.send).return_annotation == "SendResult"


def test_signal_adapter_health_satisfies_the_port_shape():
    """Replaces `test_signal_adapter_health_gap_to_adapter_health_is_known`, which
    pinned `health()` returning a bare `dict` — the platforms phase's third gap
    close, `AdapterHealth` in its place."""
    assert inspect.signature(SignalAdapter.health).return_annotation == "AdapterHealth"


def test_signal_adapter_listen_satisfies_the_port_shape():
    """`listen()` takes a keyword-only `on_message` callback over
    `NormalizedMessage`; parsing happens inside the adapter
    (`nora.platform.signal.parser.parse_event`, called from `SignalAdapter.listen`
    itself), not in `IngestPipeline`. `SignalAdapter.parse` is not on the public
    surface — the port deliberately has no `parse()` (see `nora.domain.ports`'s
    module docstring)."""
    params = inspect.signature(SignalAdapter.listen).parameters
    assert "on_message" in params
    assert params["on_message"].kind is inspect.Parameter.KEYWORD_ONLY
    assert not hasattr(SignalAdapter, "parse")


def test_signal_adapter_satisfies_platform_adapter():
    """The port's attribute is `platform`, as `SignalAdapter` has it:
    `isinstance(adapter, PlatformAdapter)` is `True` only when
    `resolve_identity()`/`send()`/`health()`/`listen()` AND the attribute all match.
    This is the headline assertion: `SignalAdapter` genuinely satisfies
    `PlatformAdapter`, not just method-by-method in isolation."""
    assert isinstance(SignalAdapter("http://x", "+1", metrics=Metrics()), PlatformAdapter)


def test_discord_adapter_satisfies_platform_adapter():
    """The registry's second concrete adapter — same headline goal as the Signal
    assertion above, proven independently rather than assumed from the Protocol
    declaration alone."""
    assert isinstance(DiscordAdapter("tok"), PlatformAdapter)


def test_whatsapp_adapter_satisfies_platform_adapter():
    """The registry's third concrete adapter — same headline goal as the Signal and
    Discord assertions above. A port never gains a second implementation without the
    shared contract suite covering it (CLAUDE.md); this is the name-level floor, and
    `test_send_contract.py`/`test_reaction_contract.py` carry the behaviour."""
    assert isinstance(WhatsAppAdapter("http://x"), PlatformAdapter)


def test_instagram_adapter_satisfies_platform_adapter():
    """The registry's fourth concrete adapter.

    It is the one adapter with a state the port does not name — DEGRADED — and it
    still has to be an ordinary `PlatformAdapter` in that state, because the whole
    point is that it stays REGISTERED. A degraded adapter that stopped satisfying the
    port would be refused by the registry after all, one layer later."""
    healthy = InstagramAdapter("http://x")
    degraded = InstagramAdapter("http://x")
    degraded.degrade("checkpointed")
    assert isinstance(healthy, PlatformAdapter)
    assert isinstance(degraded, PlatformAdapter)


def test_runtime_checkable_protocols_only_check_method_names():
    """A guard against false confidence: isinstance() against a runtime_checkable
    Protocol checks names, NOT signatures. Static checking (mypy) is what verifies
    the shapes — which is why DEC-12 adds it."""

    class Fake:
        async def ensure_workspace(self): ...
        async def ensure_peer(self, **kw): ...
        async def feed(self, **kw): ...
        async def conclude(self, **kw): ...
        async def peer_card(self, **kw): ...
        async def representation(self, **kw): ...
        async def chat(self, **kw): ...
        async def search(self, **kw): ...
        async def health(self): ...
        async def aclose(self): ...

    assert isinstance(Fake(), MemoryStore)


def test_the_media_ports_conform_in_both_implementations(tmp_path):
    """DEC-43's two new ports, both implementations each.

    `isinstance` against a `runtime_checkable` Protocol only checks method
    NAMES (see `test_runtime_checkable_protocols_only_check_method_names`
    below for the trap demonstrated directly), so this is a name-level floor.
    The SHAPE check for the production side is mypy's — `nora.infra` and
    `nora.domain` are both inside the strict override — and the BEHAVIOURAL
    check is `tests/contract/test_blob_store_contract.py` and
    `test_media_source_contract.py`, one suite over both implementations each.

    `nora.platform` is NOT inside mypy's scoped strict override, so
    `SignalMediaSource` has no static conformance proof the way
    `SqlRecordStore` does; this assertion and the contract suite are what stand
    in for it.
    """
    assert isinstance(FilesystemBlobStore(tmp_path, cipher=BlobCipher(bytes(range(32)))), BlobStore)
    assert isinstance(FakeBlobStore(), BlobStore)
    assert isinstance(SignalMediaSource(base_url="http://127.0.0.1:18080"), MediaSource)
    assert isinstance(FakeMediaSource(), MediaSource)


def test_the_reminder_stores_conform_to_the_port():
    """`ReminderStore`'s two implementations, at the name level.

    Nothing else covers this: mypy's scoped strict override lists
    `nora/domain nora/config.py nora/app nora/infra nora/presence`, so
    `nora.testing.reminders` and `tests/` are both outside it. A method renamed
    or dropped on either side would pass every other gate in silence —
    including the behavioural suite, whose `store` fixture only ever reaches the
    methods it happens to call.

    Shape stays mypy's job for the SQL side, behaviour stays
    `tests/contract/test_reminder_store_contract.py`'s for both; this is the
    floor under them.
    """
    from nora.domain.reminders import ReminderStore
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.reminders import SqlReminderStore
    from nora.testing.reminders import FakeReminderStore

    # `create_async_engine` is lazy — no connection is opened until a statement
    # runs, so this needs no database.
    engine = create_sql_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db")
    assert isinstance(SqlReminderStore(engine), ReminderStore)
    assert isinstance(FakeReminderStore(), ReminderStore)


def test_the_reverse_geocoder_backends_conform_to_the_port():
    """Both `ReverseGeocoder` backends satisfy the port at the name level (the
    shape is mypy's, the behaviour is `tests/contract/test_geocoder_contract.py`
    over both). The floor every other port gets — Google and HK-gov each."""
    from nora.domain.ports import ReverseGeocoder
    from nora.infra.geocode import GoogleGeocoder
    from nora.infra.geocode_hkgov import HKGovGeocoder

    assert isinstance(GoogleGeocoder(api_key="k"), ReverseGeocoder)
    assert isinstance(
        HKGovGeocoder(service_url="http://x/rev", token="t"),  # noqa: S106
        ReverseGeocoder,
    )


def test_hko_provider_satisfies_the_weather_provider_port():
    """`HkoWeatherProvider` is `WeatherProvider`'s only implementation; the
    port exists so another region's provider plugs in behind the `weather` tool
    with a shared contract. `isinstance` against the `runtime_checkable` Protocol
    checks the `current` method name; the behavioural contract (fail-soft, the
    current-required rule) is `tests/test_weather.py`."""
    from nora.domain.ports import WeatherProvider
    from nora.infra.weather import HkoWeatherProvider

    assert isinstance(HkoWeatherProvider(), WeatherProvider)


def test_the_media_description_stores_conform_to_the_port():
    """`MediaDescriptionStore`'s two implementations, at the name level — the
    floor every other port gets (`test_the_reminder_stores_conform_to_the_port`'s
    own precedent). Shape stays mypy's job for the SQL side (`nora.infra` is in
    the scoped strict override), behaviour is
    `tests/contract/test_media_description_store_contract.py`'s for both.

    `create_sql_engine` is lazy — no connection opens until a statement runs,
    so this needs no live database.
    """
    from nora.domain.ports import MediaDescriptionStore
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.media_descriptions import SqlMediaDescriptionStore
    from nora.testing import FakeMediaDescriptionStore

    engine = create_sql_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db")
    assert isinstance(SqlMediaDescriptionStore(engine), MediaDescriptionStore)
    assert isinstance(FakeMediaDescriptionStore(), MediaDescriptionStore)


def test_the_media_object_store_conforms_to_the_port():
    """`MediaObjectStore` — the unified media index (DEC-138) — at the name
    level, the same floor every other port gets. Behaviour is the store's own
    suite; `create_sql_engine` is lazy so this needs no live database."""
    from nora.domain.ports import MediaObjectStore
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.media_objects import SqlMediaObjectStore

    engine = create_sql_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db")
    assert isinstance(SqlMediaObjectStore(engine), MediaObjectStore)


def test_both_search_adapters_satisfy_the_search_provider_port():
    """`SearchProvider` (DEC-73, DEC-116, DEC-157) has TWO implementations, which is
    what the port is for: another backend plugs in behind
    `web_search`/`web_fetch` and the tools cannot tell which answered.

    `isinstance` against the `runtime_checkable` Protocol only checks the
    `search`/`fetch` method NAMES. The behavioural contract — fail-soft, the
    three states, the untrusted wrap and the clamp, on both verbs and both
    backends — is `tests/contract/test_search_provider_contract.py`, with each
    vendor's own wire shape in `tests/test_tavily.py` / `tests/test_exa.py`.
    """
    from nora.domain.ports import SearchProvider
    from nora.infra.search import ExaSearchProvider, TavilySearchProvider

    for impl in (TavilySearchProvider, ExaSearchProvider):
        provider = impl(api_key="k")
        assert isinstance(provider, SearchProvider), impl.__name__
        assert hasattr(provider, "fetch"), impl.__name__


def test_the_image_generator_adapter_satisfies_the_port():
    """`XaiImageGenerator` (DEC-137) is `ImageGenerator`'s
    first implementation — the name-level floor every other port gets. The
    behavioural contract (3-state result, reason mapping, fail-closed
    custody) is `tests/contract/test_image_generator_contract.py`; the shape
    is mypy's.
    """
    from nora.domain.ports import ImageGenerator
    from nora.infra.image_gen.xai_adapter import XaiImageGenerator
    from nora.infra.image_gen.xai_oauth import XaiOAuth

    oauth = XaiOAuth(refresh_token="k", state_file=Path("no-state"))  # noqa: S106
    assert isinstance(XaiImageGenerator(oauth=oauth), ImageGenerator)


def test_the_online_now_ports_conform_in_both_implementations():
    """`SteamPresenceProvider` and `VoicePresenceSource`,
    both implementations each — the name-level floor every other port gets.
    `DiscordVoicePresence(adapter=DiscordAdapter("tok"))` constructs without a
    live client the same way `test_discord_adapter_satisfies_platform_adapter`
    constructs the adapter itself. Behaviour is
    `tests/contract/test_steam_presence_contract.py` and
    `test_voice_presence_contract.py`, one suite over both implementations
    each; the shape is mypy's where the override reaches.
    """
    from nora.domain.ports import SteamPresenceProvider, VoicePresenceSource
    from nora.infra.steam import SteamPresenceClient
    from nora.platform.discord.voice_presence import DiscordVoicePresence
    from nora.testing import FakeSteamPresenceProvider, FakeVoicePresenceSource

    assert isinstance(SteamPresenceClient(api_key="k"), SteamPresenceProvider)
    assert isinstance(FakeSteamPresenceProvider(), SteamPresenceProvider)
    assert isinstance(
        DiscordVoicePresence(adapter=DiscordAdapter("tok")), VoicePresenceSource,
    )
    assert isinstance(FakeVoicePresenceSource(), VoicePresenceSource)


def test_the_video_reading_stores_conform_to_the_port():
    """`VideoReadingStore`'s two implementations, at the name level; behaviour
    is `tests/contract/test_video_reading_store_contract.py`'s for both."""
    from nora.domain.ports import VideoReadingStore
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.video_readings import SqlVideoReadingStore
    from nora.testing import FakeVideoReadingStore

    engine = create_sql_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db")
    assert isinstance(SqlVideoReadingStore(engine), VideoReadingStore)
    assert isinstance(FakeVideoReadingStore(), VideoReadingStore)
