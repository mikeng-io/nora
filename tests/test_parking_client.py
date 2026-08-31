"""ParkingClient — real-time HK vacancy from the TD feed (audit 2026-08-31).

Pins the index parse + name matching + vacancy extraction against the REAL
feed shapes (captured in the tests as samples so no live network is needed).
"""
from __future__ import annotations

import pytest

from nora.infra.parking.client import (
    _lastupdate_of,
    _normalise,
    _vacancy_of,
    ParkingClient,
)

INDEX_SAMPLE = (
    "Parking ID,Car Park Name (English),Car Park Name (Traditional Chinese),"
    "Car Park Name (Simplified Chinese),Basic Information,Parking Vacancy Data\n"
    "TDC10P1,Sai Kung Government Offices Carpark,西貢政府合署停車場,"
    "西贡政府合署停车场,https://resource.data.one.gov.hk/td/carpark/"
    "basic_info_tdc10p1.json,https://resource.data.one.gov.hk/td/carpark/"
    "vacancy_tdc10p1.json\n"
    "TDC121P1,Airside,Airside,Airside,https://resource.data.one.gov.hk/td/"
    "carpark/basic_info_tdc121p1.json,https://resource.data.one.gov.hk/td/"
    "carpark/vacancy_tdc121p1.json\n"
)

VACANCY_SAMPLE = {
    "car_park": [{
        "park_id": "tdc10p1",
        "vehicle_type": [{
            "type": "P",
            "service_category": [{
                "category": "HOURLY", "vacancy_type": "A",
                "vacancy": 30, "lastupdate": "2026-08-31 11:35:34",
            }],
        }],
    }],
}


class _FakeSession:
    def __init__(self, *args, **kwargs):
        self._index = kwargs.get("index", INDEX_SAMPLE)
        self._vacancy = kwargs.get("vacancy", dict(VACANCY_SAMPLE))
        self._basic = kwargs.get("basic", None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url: str):  # NOT async — aiohttp's get() returns a context manager
        return _FakeResp(url, self)


class _FakeResp:
    def __init__(self, url: str, session: _FakeSession):
        self._url = url
        self._session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self, **kwargs):
        if "car_park_list" in self._url:
            return self._session._index
        raise AssertionError(f"unexpected index url {self._url}")

    async def json(self, **kwargs):
        if "vacancy_" in self._url:
            return self._session._vacancy
        if "basic_info_" in self._url:
            return self._session._basic or {"car_park": [{}]}
        raise AssertionError(f"unexpected url {self._url}")


@pytest.mark.asyncio
async def test_normalise_strips_space_and_case():
    assert _normalise("Pacific Place") == "pacificplace"
    assert _normalise(" 太古廣場 ") == "太古廣場"


@pytest.mark.asyncio
async def test_vacancy_of_first_hourly():
    assert _vacancy_of(VACANCY_SAMPLE) == 30
    assert _vacancy_of({"car_park": []}) is None


@pytest.mark.asyncio
async def test_lastupdate_of_first_hourly():
    assert _lastupdate_of(VACANCY_SAMPLE) == "2026-08-31 11:35:34"


@pytest.mark.asyncio
async def test_parking_client_matches_venue_and_returns_vacancy(monkeypatch):
    client = ParkingClient()
    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    out = await client.vacancy(name="西貢政府合署停車場")
    assert out is not None
    assert out["parking_id"] == "TDC10P1"
    assert out["vacancy"] == 30
    assert out["lastupdate"] == "2026-08-31 11:35:34"


@pytest.mark.asyncio
async def test_parking_client_substring_match(monkeypatch):
    # "Sai Kung" is a distinctive substring of the EN name.
    client = ParkingClient()
    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    out = await client.vacancy(name="Sai Kung")
    assert out is not None and out["parking_id"] == "TDC10P1"


@pytest.mark.asyncio
async def test_parking_client_unknown_returns_none(monkeypatch):
    client = ParkingClient()
    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    assert await client.vacancy(name="月球停車場") is None

BASIC_SAMPLE = {"car_park": [{
    "park_id": "tdc10p1",
    "opening_status": "OPEN",
    "displayAddress_en": "Man Nin Street No 24, Sai Kung",
    "remark_en": "Height Limit: 2.45m; Per Hours: $27; Night: $90",
}]}


@pytest.mark.asyncio
async def test_parking_client_carries_basic_info_bonus(monkeypatch):
    """opening_status/address/fee come from the basic-info JSON when present —
    never hardcoded (fix 2026-08-31: was a fabricated 'OPEN')."""
    client = ParkingClient()
    monkeypatch.setattr(
        "aiohttp.ClientSession",
        lambda *a, **k: _FakeSession(*a, **{**k, "basic": BASIC_SAMPLE}),
    )
    out = await client.vacancy(name="西貢政府合署停車場")
    assert out is not None
    # The R-27 axis owns the `status` key (Kimi audit 2026-08-31, blocker):
    # the feed's opening status rides `opening_status`, never `status`, so a
    # `{"status": "ok", **data}` spread cannot overwrite the contract's `ok`.
    assert out["opening_status"] == "OPEN"
    assert "status" not in out
    assert out["address"] == "Man Nin Street No 24, Sai Kung"
    assert "Height Limit" in out["fee"]


@pytest.mark.asyncio
async def test_parking_client_no_status_when_basic_unavailable(monkeypatch):
    """A missing/empty basic-info must NOT invent an opening status (R-5)."""
    client = ParkingClient()
    monkeypatch.setattr(
        "aiohttp.ClientSession",
        lambda *a, **k: _FakeSession(*a, **{**k, "basic": None}),
    )
    out = await client.vacancy(name="西貢政府合署停車場")
    assert "status" not in out
    assert out["vacancy"] == 30  # vacancy survives the basic-info miss
