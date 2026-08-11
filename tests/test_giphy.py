import aiohttp

from nora.domain.media import GifCandidate
from nora.infra.giphy import GiphyClient


class _Content:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _: int):
        for chunk in self._chunks:
            yield chunk


class _Response:
    def __init__(
        self,
        *,
        status: int = 200,
        body: object = None,
        headers: dict[str, str] | None = None,
        chunks: tuple[bytes, ...] = (),
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.content = _Content(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def json(self):
        return self._body


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.urls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def get(self, url: str, **_):
        self.urls.append(url)
        return self._responses.pop(0)


def _gif(
    *, identifier: str = "chosen", url: str = "https://media.giphy.com/media/chosen/giphy.gif"
) -> dict:
    return {
        "id": identifier,
        "rating": "g",
        "title": "Happy dance",
        "alt_text": "A person dances happily",
        "tags": ["happy", "dance"],
        "images": {"downsized": {"url": url}},
    }


async def test_search_returns_only_bounded_described_safe_candidates(monkeypatch):
    session = _Session(
        [
            _Response(
                body={
                    "data": [
                        _gif(),
                        {"id": "adult", "rating": "r", "title": "no"},
                        {"id": "blank", "rating": "g"},
                    ]
                }
            )
        ]
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)

    candidates = await GiphyClient(api_key="test").search(query="happy dance")

    assert candidates == (
        GifCandidate(
            "giphy", "chosen", "Happy dance", "A person dances happily", ("happy", "dance"), "g"
        ),
    )


async def test_fetch_binds_selected_metadata_and_rejects_non_giphy_media(monkeypatch):
    candidate = GifCandidate(
        "giphy", "chosen", "Happy dance", "A person dances happily", ("happy", "dance"), "g"
    )
    session = _Session([_Response(body={"data": _gif(identifier="other")})])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)
    assert await GiphyClient(api_key="test").fetch(candidate=candidate) is None
    assert len(session.urls) == 1

    session = _Session([_Response(body={"data": _gif(url="https://example.test/gif")})])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)
    assert await GiphyClient(api_key="test").fetch(candidate=candidate) is None
    assert len(session.urls) == 1


async def test_fetch_streams_and_rejects_oversized_gifs(monkeypatch):
    candidate = GifCandidate(
        "giphy", "chosen", "Happy dance", "A person dances happily", ("happy", "dance"), "g"
    )
    session = _Session(
        [
            _Response(body={"data": _gif()}),
            _Response(chunks=(b"GIF89a", b"x" * 10)),
        ]
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)
    attachment = await GiphyClient(api_key="test", max_bytes=16).fetch(candidate=candidate)
    assert attachment is not None
    assert attachment.content == b"GIF89a" + b"x" * 10

    session = _Session(
        [
            _Response(body={"data": _gif()}),
            _Response(chunks=(b"GIF89a", b"x" * 11)),
        ]
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)
    assert await GiphyClient(api_key="test", max_bytes=16).fetch(candidate=candidate) is None
