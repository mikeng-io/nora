from unittest.mock import patch

import pytest

from skills.geocode.render import GeocodeError, geocode_address


def test_a_known_address_returns_its_coordinate(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 22.4106, "lng": 114.1245}}}],
        }
        result = geocode_address(address="1 Larkspur Rd, Tsim Sha Tsui")
    assert result == (22.4106, 114.1245)


def test_the_address_is_sent_as_the_query_param(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 1.0, "lng": 2.0}}}],
        }
        geocode_address(address="Yoho Mall, Yuen Long")
    params = mock_get.call_args.kwargs["params"]
    assert params["address"] == "Yoho Mall, Yuen Long"


def test_an_address_google_cannot_resolve_raises_geocode_error(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "ZERO_RESULTS",
            "results": [],
        }
        with pytest.raises(GeocodeError):
            geocode_address(address="nowhere at all")


def test_raise_for_status_is_called_before_the_json_is_parsed(monkeypatch):
    """Revert-sensitive on the actual line, not on `requests`' own well-tested
    behaviour: delete `response.raise_for_status()` from the skill and this
    goes red, because nothing else in the function calls it. (A prior draft
    of this test set `status_code = 403` and asserted `pytest.raises
    (Exception)` — but `GeocodeError` (raised on a non-OK JSON `status`) IS
    an `Exception`, so that assertion stayed green even with the
    `raise_for_status()` call deleted; it also never read `status_code` at
    all. `assert_called_once()` on the mock is the only assertion that
    actually pins the call happening.)"""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    with patch("skills.geocode.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "OK",
            "results": [{"geometry": {"location": {"lat": 1.0, "lng": 2.0}}}],
        }
        geocode_address(address="1 Larkspur Rd")
    mock_get.return_value.raise_for_status.assert_called_once()
