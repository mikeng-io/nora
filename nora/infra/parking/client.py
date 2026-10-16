"""`ParkingClient` — real-time HK car-park vacancy behind the `parking_status`
tool, from the Transport Department's open-data feed (data.gov.hk, hk-td-tis_5).

The feed is a small index plus one tiny JSON per car park:
- `car_park_list.csv` (~105KB, hundreds of carparks): Parking ID, name EN/TC/SC,
  basic-info URL, vacancy URL.
- Per-parking vacancy JSON (~179 bytes): `{"car_park":[{"park_id":"...",
  "vehicle_type":[{"type":"P","service_category":[{"category":"HOURLY",
  "vacancy_type":"A","vacancy":30,"lastupdate":"..."}]}]}]}`.
- Per-parking basic-info JSON (~500 bytes): displayAddress, latitude/longitude,
  district, opening_status, height, remark (rate table).

The MODEL never sees the 105KB index: the client caches it in-process and
matches a venue name to ONE parking id, then fetches that one small JSON.
Same posture as `TransitClient` — nothing here raises; a failure is
`None` (the tool reports `unavailable`), never a fabricated number.

URLs and timeout are constants here (published government endpoints) but
constructor parameters so tests can point at stand-ins.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import aiohttp

log = logging.getLogger("nora.parking")

INDEX_URL = "https://static.data.gov.hk/td/parking-vacancy/dataspec/car_park_list.csv"
RESOURCE_URL = "https://resource.data.one.gov.hk/td/carpark/"

TIMEOUT_SECONDS = 5.0

# How many normalised characters a fallback match needs before it may bind:
# a 1-char query like 's' would substring-match a dozen
# parking names and attach the wrong vacancy. A real venue name is at least
# ~4 ASCII chars («park», «central») or 2 CJK chars («大埔»), which this
# scale expresses: each CJK char counts double.
_MIN_MATCH_LEN = 4


class ParkingClient:
    """Live HK car-park vacancy, one named venue at a time.

    Constructed once per process; the index CSV cache lives on it so a
    repeated question does not re-pull 105KB every time.
    """

    def __init__(
        self,
        *,
        index_url: str = INDEX_URL,
        resource_url: str = RESOURCE_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._index_url = index_url
        self._resource_url = resource_url.rstrip("/") + "/"
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        # name-normalised -> (parking_id, name_en, name_tc, vacancy_url, basic_url)
        self._index: dict[str, tuple[str, str, str, str, str]] | None = None

    async def vacancy(self, *, name: str, strict: bool = False) -> dict[str, Any] | None:
        """Vacancy for one named venue, or None (unavailable / not found).

        `strict=True` (find_parking's cross-ref) requires an EXACT index
        match — a Google-named lot must never attach another car park's
        vacancy through the substring fallback. `parking_status` (a user
        naming a spot) keeps the fallback, where a distinctive substring is
        the user experience.

        The caller distinguishes `nothing_found` from
        `unavailable`; this returns `None` for both, matching the
        `ParkingProvider` port's "failure is a missing reading" shape.
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return None
        try:
            index = await self._load_index()
        except Exception:
            log.exception("parking index fetch failed")
            return None
        if index is None:
            return None
        hit = self._match(index, cleaned, strict=strict)
        if hit is None:
            return None
        parking_id, name_en, name_tc, vacancy_url, basic_url = hit
        try:
            async with aiohttp.ClientSession(
                timeout=self._timeout, raise_for_status=True,
            ) as session, session.get(vacancy_url) as resp:
                # utf-8-sig: the vacancy bodies carry a BOM too (same feed
                # convention as the index CSV).
                body = await resp.json(encoding="utf-8-sig")
        except Exception:
            log.exception("parking vacancy fetch failed")
            return None
        vacancy = _vacancy_of(body)
        if vacancy is None:
            return None
        # Basic-info is a best-effort BONUS (opening status, address, fee) —
        # a failure here must not cost a vacancy the feed already answered,
        # and the fields appear only when actually read (never invented).
        opening_status: str | None = None
        address: str | None = None
        fee: str | None = None
        if basic_url:
            try:
                async with aiohttp.ClientSession(
                    timeout=self._timeout, raise_for_status=True,
                ) as session, session.get(basic_url) as resp:
                    basic = await resp.json(encoding="utf-8-sig")
                # The basic-info envelope is {"car_park": [{...}]} — the
                # first record is the one this index row named.
                row = None
                if isinstance(basic, dict):
                    cp = basic.get("car_park")
                    if isinstance(cp, list) and cp and isinstance(cp[0], dict):
                        row = cp[0]
                opening_status = _basic_text(row, "opening_status")
                address = (_basic_text(row, "displayAddress_en")
                           or _basic_text(row, "displayAddress_tc"))
                fee = _basic_text(row, "remark_en") or _basic_text(row, "remark_tc")
            except Exception:
                log.exception("parking basic-info fetch failed (vacancy kept)")
        out: dict[str, object] = {
            "parking_id": parking_id,
            "name": name_tc or name_en,
            "vacancy": vacancy,
            "lastupdate": _lastupdate_of(body),
        }
        if opening_status:
            # `opening_status`, NOT `status`: the R-27 axis owns the `status`
            # key, and `parking_status` spreads this dict under its own
            # `{"status": "ok", **data}` — a feed value written into `status`
            # would overwrite the contract's `ok` and fail the output-contract
            # validator. The opening status
            # rides its own key; the model reads it as a bonus field.
            out["opening_status"] = opening_status
        if address:
            out["address"] = address
        if fee:
            out["fee"] = fee
        return out

    async def _load_index(self) -> dict[str, tuple[str, str, str, str, str]] | None:
        if self._index is not None:
            return self._index
        async with aiohttp.ClientSession(
            timeout=self._timeout, raise_for_status=True,
        ) as session, session.get(self._index_url) as resp:
            # utf-8-sig strips the CSV's BOM — otherwise the first header
            # is "\ufeffParking ID" and `row.get("Parking ID")` misses
            # every row, leaving the index empty.
            text = await resp.text(encoding="utf-8-sig")
        index: dict[str, tuple[str, str, str, str, str]] = {}
        for row in csv.DictReader(io.StringIO(text)):
            pid = (row.get("Parking ID") or "").strip()
            name_en = (row.get("Car Park Name (English)") or "").strip()
            name_tc = (row.get("Car Park Name (Traditional Chinese)") or "").strip()
            vacancy_url = (row.get("Parking Vacancy Data") or "").strip()
            basic_url = (row.get("Basic Information") or "").strip()
            if not pid or not name_en or not vacancy_url:
                continue
            # Register all three name columns (normalised) -> same record.
            for label in (name_en, name_tc, name_en.lower()):
                key = _normalise(label)
                if key:
                    index[key] = (pid, name_en, name_tc, vacancy_url, basic_url)
        if not index:
            return None
        self._index = index
        return index

    def _match(
        self, index: dict[str, tuple[str, str, str, str, str]], name: str,
        *, strict: bool = False,
    ) -> tuple[str, str, str, str, str] | None:
        key = _normalise(name)
        if key in index:
            return index[key]
        # Cross-ref (strict) never falls back: another lot's vacancy must
        # not ride a Google-named entry through a loose substring.
        if strict:
            return None
        # A too-short or empty query must not fall back: 's' would substring-
        # match a dozen parking names and attach the WRONG vacancy ('s' →
        # Stanley Plaza). CJK chars count double, so «大埔» passes while 's' does not.
        if _weighted_len(key) < _MIN_MATCH_LEN:
            return None
        # Distinctive-substring fallback: a venue name usually CONTAINS the
        # car park's name («太古廣場» → «Pacific Place»/「太古廣場」) or vice
        # versa. Two guardrails: a MINIMUM matched length
        # so a 1-char query ('s') cannot bind Stanley Plaza, and a
        # DETERMINISTIC win — the SHORTEST containing name wins equal-length
        # scores, since dict order would pick an arbitrary sibling ('Central'
        # → Park Central over Central Government Offices).
        best: tuple[int, int, tuple[str, str, str, str, str]] | None = None
        for k, rec in index.items():
            if not k or len(k) < _MIN_MATCH_LEN:
                continue
            if key and (key in k or k in key):
                score = min(len(key), len(k))
                # Win on score; on equal scores the SHORTER containing key
                # wins — the tighter containment is the query itself, not a
                # wrapper phrase around it.
                if best is None or score > best[0] or (
                    score == best[0] and len(k) < best[1]
                ):
                    best = (score, len(k), rec)
        return best[2] if best else None


def _normalise(text: str) -> str:
    """Lowercase, strip spaces/CJK gaps — «Pacific Place» == «pacificplace»."""
    return "".join(ch for ch in text.lower() if not ch.isspace())


def _weighted_len(text: str) -> int:
    """CJK chars count double — «大埔» weighs 4 (a real place) while 's'
    weighs 1 (noise)."""
    return sum(2 if "\u4e00" <= ch <= "\u9fff" else 1 for ch in text)


def _vacancy_of(body: Any) -> int | None:
    """First HOURLY vacancy count from the feed body, or None."""
    try:
        for cp in body.get("car_park", []):
            for vt in cp.get("vehicle_type", []):
                for sc in vt.get("service_category", []):
                    if sc.get("category") == "HOURLY":
                        v = sc.get("vacancy")
                        # Negative / zero sentinel must not render as an
                        # impossible count.
                        if isinstance(v, int) and v >= 0:
                            return v
    except (AttributeError, TypeError):
        # A wrong-shaped feed body (non-dict, non-iterable) is "no reading".
        return None
    return None


def _lastupdate_of(body: Any) -> str | None:
    try:
        for cp in body.get("car_park", []):
            for vt in cp.get("vehicle_type", []):
                for sc in vt.get("service_category", []):
                    if sc.get("category") == "HOURLY" and sc.get("lastupdate"):
                        return str(sc["lastupdate"])
    except (AttributeError, TypeError):
        # A wrong-shaped feed body (non-dict, non-iterable) is "no reading".
        return None
    return None

def _basic_text(basic: Any, key: str) -> str | None:
    """A string field from the basic-info JSON, or None when missing/not a
    string — the bonus fields must never be invented from a wrong shape."""
    if not isinstance(basic, dict):
        return None
    value = basic.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
