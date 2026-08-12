from unittest.mock import patch

from skills.maps.render import render_map


def test_a_single_location_returns_an_inline_image_tag():
    with patch("skills.maps.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b"fake-png-bytes"
        result = render_map(locations=[(22.4106, 114.1245)])
    assert result.startswith("<img")
    assert "data:image/png;base64," in result


def test_multiple_locations_are_passed_as_markers():
    with patch("skills.maps.render.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b"fake-png-bytes"
        render_map(locations=[(22.4106, 114.1245), (22.2793, 114.1628)])
    params = mock_get.call_args.kwargs["params"]
    assert params["markers"].count("|") >= 1  # at least two markers joined
