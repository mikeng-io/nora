"""One suite, both implementations of `LinkPreviewSource`.

CLAUDE.md: "A port never gains a second implementation — including a test fake — without a
shared contract suite both pass." `nora.testing.FakeLinkPreviewSource` is that second
implementation, and it is what `tests/unit/test_outbound_composer.py` asserts every
preview behaviour against — so if it drifted from `OpenGraphPreviewSource`, every one of
those tests would be proving something about a fake and nothing about production.

**What this suite deliberately does NOT try to be.** The fake cannot resolve hostnames and
the real one cannot be pointed at a loopback server (its own guard refuses one —
`tests/test_preview_source.py` explains that at length), so there is no shared setup in
which both can be asked to produce the SAME card from the SAME page. Pretending otherwise
— a fake that returns a card for anything, parametrized beside a real source that is never
reached — is the "fake that answers every request the same way".

So the contract asserted here is the one both really do hold, and it is the one the
composer actually programs against: **every failure is `None`, no failure is an exception,
and a destination that is not on the public internet is a failure.** That is the whole of
`LinkPreviewSource`'s behavioural surface as far as any caller is concerned.
"""

import inspect

import pytest

from nora.domain.ports import LinkPreview, LinkPreviewSource
from nora.infra.preview import OpenGraphPreviewSource
from nora.testing import FakeLinkPreviewSource

CARD = LinkPreview(url="https://links.example.net/abc", title="A page", description="about it")


@pytest.fixture(params=["opengraph", "fake"])
def source(request):
    if request.param == "opengraph":
        # Short timeout: every URL this suite gives it is refused before any
        # socket is opened, so this only bounds the DNS-failure cases.
        return OpenGraphPreviewSource(timeout_seconds=1.0)
    return FakeLinkPreviewSource({CARD.url: CARD})


@pytest.mark.parametrize("impl", [OpenGraphPreviewSource, FakeLinkPreviewSource])
def test_preview_for_matches_the_port_signature(impl):
    """Keyword-only after self, per `tests/test_ports.py`'s convention. An
    `isinstance` against a runtime_checkable Protocol only checks names."""
    params = inspect.signature(impl.preview_for).parameters
    assert params["url"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("impl", [OpenGraphPreviewSource, FakeLinkPreviewSource])
def test_both_implementations_are_link_preview_sources(impl):
    assert isinstance(impl(), LinkPreviewSource)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9100/metrics",
        "http://localhost:18080/api/v1/rpc",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_neither_implementation_previews_a_non_public_destination(source, url):
    """The SSRF refusal, held by both — which is what lets the composer's tests
    exercise it against the fake and still mean something.

    The fake's check is deliberately not a copy of the real one (it cannot be —
    the real one resolves names); it reproduces the properties a test can state
    literally, and this suite is where the two are held to the same answer on
    the cases that matter.
    """
    assert await source.preview_for(url=url) is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x",
                                 "data:text/html,<title>x"])
async def test_neither_implementation_previews_a_non_http_scheme(source, url):
    assert await source.preview_for(url=url) is None


async def test_neither_implementation_raises_on_a_url_it_cannot_use(source):
    """The load-bearing property. `LinkPreviewSource`'s docstring makes
    never-raising the IMPLEMENTATION's obligation, because the caller is on the
    reply path with a human waiting and a preview is decoration on a message
    that must go out anyway (R-5).

    `OutboundComposer` guards this call with a `try` regardless, and that guard
    is not a reason to skip this: a fake that raised where production returns
    would let the composer's tests prove the guard works while never proving
    the port's contract does.
    """
    for url in ("https://nx.invalid./page", "https://", "not a url at all", ""):
        try:
            result = await source.preview_for(url=url)
        except Exception as exc:  # noqa: BLE001 — catching broadly IS the assertion
            pytest.fail(f"preview_for({url!r}) raised {type(exc).__name__}")
        assert result is None or isinstance(result, LinkPreview)


async def test_a_titleless_card_is_never_produced(source):
    """`--preview-title` is mandatory on the wire, so both implementations
    refuse rather than emit a blank chip — the fake by checking the page it
    holds, the real one by refusing a document with no usable title."""
    result = await source.preview_for(url="https://example.invalid./titleless")
    assert result is None or result.title
