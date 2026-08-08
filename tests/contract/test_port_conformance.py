"""Conformance: today's concrete classes must satisfy the ports they'll be injected as.

This is what makes the ports real rather than aspirational. Where a class does NOT
yet conform, the gap is asserted explicitly with the phase that closes it — so the
divergence is tracked, not forgotten.

No known-gap pin remains here for `PlatformAdapter`: the platforms phase closed all
five gaps between it and `SignalAdapter` (four behavioural — `resolve_identity()`,
`send()`, `health()`, `listen()` — plus a fifth found while closing those four: the
port declared `name: str` where every concrete type said `platform`).
`test_signal_adapter_satisfies_platform_adapter` below is the phase's real
acceptance test — the point of the whole phase was making this `isinstance` call
return `True`.
"""

import inspect

from nora.domain.ports import BlobStore, MediaSource, MemoryStore, PlatformAdapter
from nora.infra.blobs import FilesystemBlobStore
from nora.infra.blobs.cipher import BlobCipher
from nora.infra.honcho import HonchoClient
from nora.obs.metrics import Metrics
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.media import SignalMediaSource
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
    port, so it does not carry `tests/fakes.py::FakeHoncho`'s older
    positional-argument drift."""
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
    """Replaces `test_signal_adapter_listen_gap_to_on_message_is_known`, which pinned
    `listen()` taking a positional `on_event` dict callback — the fourth of the
    platforms phase's five gaps closing. `listen()` now takes a keyword-only
    `on_message` callback over `NormalizedMessage`; parsing moved inside the adapter
    (`nora.platform.signal.parser.parse_event`, called from `SignalAdapter.listen`
    itself), off `IngestPipeline` entirely (Trap B). `SignalAdapter.parse` came off
    the public surface as part of the same change — the port deliberately has no
    `parse()` (see `nora.domain.ports`'s module docstring)."""
    params = inspect.signature(SignalAdapter.listen).parameters
    assert "on_message" in params
    assert params["on_message"].kind is inspect.Parameter.KEYWORD_ONLY
    assert not hasattr(SignalAdapter, "parse")


def test_signal_adapter_satisfies_platform_adapter():
    """The fifth gap, found while closing the other four (see this module's
    docstring): the port declared `name: str`, `SignalAdapter` has `platform`.
    `isinstance(adapter, PlatformAdapter)` was `False` even after
    `resolve_identity()`/`send()`/`health()`/`listen()` all matched — this is
    the assertion that was actually `False` until the port's attribute was
    renamed to `platform`. This IS the phase's headline goal: `SignalAdapter`
    genuinely satisfies `PlatformAdapter`, not just method-by-method in
    isolation."""
    assert isinstance(SignalAdapter("http://x", "+1", metrics=Metrics()), PlatformAdapter)


def test_runtime_checkable_protocols_only_check_method_names():
    """A guard against false confidence: isinstance() against a runtime_checkable
    Protocol checks names, NOT signatures. Static checking (mypy) is what verifies
    the shapes — which is why DEC-12 adds it."""

    class Fake:
        async def ensure_workspace(self): ...
        async def ensure_peer(self, **kw): ...
        async def feed(self, **kw): ...
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

    This entry was MISSING while every other port had one, and the gap was not
    covered by anything else: mypy's scoped strict override lists
    `nora/domain nora/config.py nora/app nora/infra nora/presence`, so
    `nora.testing.reminders` and `tests/` are both outside it. A method renamed
    or dropped on either side would have passed all four gates in silence —
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
    """`HkoWeatherProvider` is `WeatherProvider`'s only implementation today; the
    port exists so another region's provider plugs in behind the `weather` tool
    with a shared contract. `isinstance` against the `runtime_checkable` Protocol
    checks the `current` method name; the behavioural contract (fail-soft, the
    current-required rule) is `tests/test_weather.py`."""
    from nora.domain.ports import WeatherProvider
    from nora.infra.weather import HkoWeatherProvider

    assert isinstance(HkoWeatherProvider(), WeatherProvider)


def test_tavily_provider_satisfies_the_search_provider_port():
    """`TavilySearchProvider` is `SearchProvider`'s only implementation today; the
    port (DEC-73) exists so another search backend plugs in behind `web_search`
    with a shared contract. `isinstance` against the `runtime_checkable` Protocol
    checks the `search` method name; the behavioural contract (fail-soft,
    untrusted wrap, clamp) is `tests/test_tavily.py`."""
    from nora.domain.ports import SearchProvider
    from nora.infra.search import TavilySearchProvider

    assert isinstance(TavilySearchProvider(api_key="k"), SearchProvider)
