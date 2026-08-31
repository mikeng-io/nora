"""StreetParkingClient — TD metered on-street spaces (hk-td-msd_1).

Pins the parse of the two real CSVs (spaces with lat/lng, occupancy
status) and the distance filter — without live network.
"""
from __future__ import annotations

import pytest

from nora.infra.parking.street import StreetParkingClient, _metres

SPACES_CSV = "\ufeffPoleId,ParkingSpaceId,Region,District,Street,Street_tc,SectionOfStreet,Latitude,Longitude,VehicleType\n" \
    "P1,S10006A,HK,Central,QUEEN'S ROAD CENTRAL,皇后大道中,Section,22.2820,114.1580,M\n" \
    "P1,S10007B,HK,Central,QUEEN'S ROAD CENTRAL,皇后大道中,Section,22.2821,114.1581,M\n" \
    "P2,S20001A,NT,Tsuen Wan,TAI PA STREET,大埔道,Section,22.3750,114.1100,M\n" \
    "P2,S20001C,NT,Tsuen Wan,TAI PA STREET,大埔道,Section,22.3751,114.1101,M\n"

OCC_CSV = "\ufeffParkingSpaceId,ParkingMeterStatus,OccupancyStatus,OccupancyDateChanged\n" \
    "S10006A,N,O,08/31/2026 04:07:43 PM\n" \
    "S10007B,N,V,08/31/2026 12:55:46 PM\n" \
    "S20001A,N,V,08/31/2026 11:00:00 AM\n" \
    "S20001C,N,O,08/31/2026 09:30:00 AM\n"


class _FakeResp:
    def __init__(self, text: str, url: str):
        self._text = text
        self._url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def text(self, encoding="utf-8"):
        value = self._text
        if encoding == "utf-8-sig" and value.startswith("\ufeff"):
            value = value[1:]
        return _FakeAwait(value)


class _FakeAwait:
    def __init__(self, value: str):
        self._value = value

    def __await__(self):
        async def _inner():
            return self._value
        return _inner().__await__()


class _FakeSession:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url: str):
        if "spaceinfo" in url:
            return _FakeResp(SPACES_CSV, url)
        if "occupancystatus" in url:
            return _FakeResp(OCC_CSV, url)
        raise AssertionError(f"unexpected url {url}")


@pytest.mark.asyncio
async def test_street_near_counts_and_streets(monkeypatch):
    client = StreetParkingClient()
    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    out = await client.near(lat=22.2820, lng=114.1580, radius_m=500)
    assert out is not None
    assert out.total == 2  # the two Central bays
    assert out.vacant == 1  # S10007B is V
    assert out.streets == ("皇后大道中",)


@pytest.mark.asyncio
async def test_street_near_empty_outside_radius(monkeypatch):
    client = StreetParkingClient()
    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    # Far from Central — Tsuen Wan bays are >5km away, none within 500m
    out = await client.near(lat=22.28, lng=114.15, radius_m=200)
    assert out is not None
    assert out.total == 0 and out.vacant == 0


def test_metres_basic():
    d = _metres(22.2820, 114.1580, 22.2821, 114.1581)
    assert 10 < d < 30