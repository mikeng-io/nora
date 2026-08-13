"""`rendering.content_disposition`/`is_inline` — the inline-vs-attachment and
filename policy `GET /drop/<slug>` renders artifacts through (server.py's
module docstring, task 4)."""

from __future__ import annotations

from rendering import content_disposition, is_inline


def test_images_are_inline():
    assert is_inline("image/png")
    assert is_inline("image/jpeg")


def test_pdf_is_inline():
    assert is_inline("application/pdf")


def test_text_audio_and_video_are_inline():
    assert is_inline("text/plain")
    assert is_inline("audio/mpeg")
    assert is_inline("video/mp4")


def test_a_media_type_parameter_does_not_defeat_the_check():
    """`text/plain; charset=utf-8` is still `text/plain` for this purpose."""
    assert is_inline("text/plain; charset=utf-8")


def test_an_unknown_media_type_is_not_inline():
    assert not is_inline("application/octet-stream")
    assert not is_inline("application/zip")
    assert not is_inline("application/vnd.ms-word")


def test_inline_disposition_includes_the_filename():
    header = content_disposition("image/png", "My Screenshot")
    assert header.startswith("inline;")
    assert 'filename="My Screenshot.png"' in header


def test_attachment_disposition_for_an_unknown_type():
    header = content_disposition("application/zip", "archive")
    assert header.startswith("attachment;")
    assert "archive" in header


def test_an_extension_already_present_is_not_duplicated():
    header = content_disposition("application/pdf", "report.pdf")
    assert 'filename="report.pdf"' in header


def test_unsafe_filename_characters_are_stripped():
    header = content_disposition("text/plain", 'evil"; filename="x.txt')
    # Neither a raw quote nor a stray `filename=` survives into the header.
    assert header.count('"') == 2
    assert "evil" in header


def test_control_characters_are_stripped_from_the_filename():
    header = content_disposition("text/plain", "line1\r\nSet-Cookie: x=y")
    assert "\r" not in header
    assert "\n" not in header


def test_an_empty_title_falls_back_to_a_default_name():
    header = content_disposition("text/plain", "")
    assert 'filename="artifact.txt"' in header


def test_a_very_long_title_is_truncated():
    header = content_disposition("text/plain", "x" * 500)
    # 200-char cap (rendering.py's own constant) plus disposition/quoting.
    assert len(header) < 260


def test_a_non_ascii_title_does_not_crash_and_stays_latin1_safe():
    """Regression: Starlette's `Response` encodes header VALUES as latin-1
    (`starlette.responses.Response.init_headers`) — a title outside that
    range used to reach the header unfolded and raise `UnicodeEncodeError`
    on every future render, not at registration time. This must never
    produce a value `str.encode("latin-1")` can't handle."""
    for title in ("日本語のタイトル", "party 🎉", "Ω config", "café résumé"):
        header = content_disposition("text/plain", title)
        header.encode("latin-1")  # must not raise


def test_truncation_never_cuts_into_the_appended_extension():
    """Regression: truncating "base + ext" AFTER appending the extension
    could slice the extension itself off near the length boundary. The
    extension must always survive intact."""
    header = content_disposition("application/pdf", "x" * 199)
    assert header.endswith('.pdf"')
