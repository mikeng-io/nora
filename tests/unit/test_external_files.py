"""`nora.domain.external_files`: recognising a shared file link, rendering it as
a link handle, the stored form, and resolving a code."""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from nora.domain.external_files import (
    ExternalFileRef,
    external_files_json,
    file_refs_in,
    link_handles_in,
    recognise,
    refs_from_external_files_json,
    render_file_links,
    resolve_file_code,
)
from nora.domain.media import handle_hex
from nora.domain.models import NormalizedMessage
from nora.testing.fakes import FakeRecordStore

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
OTHER = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa98765"
HANDLE = hashlib.sha256(f"google_drive:{ID}".encode()).hexdigest()[:6]


@pytest.mark.parametrize(("url", "label"), [
    (f"https://docs.google.com/document/d/{ID}/edit?usp=sharing", "Google Doc link"),
    (f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0", "Google Sheet link"),
    (f"https://docs.google.com/presentation/d/{ID}/view", "Google Slides link"),
    (f"https://docs.google.com/document/u/1/d/{ID}/edit", "Google Doc link"),
    (f"https://drive.google.com/file/d/{ID}/view?usp=drive_link", "Google Drive link"),
    (f"https://drive.google.com/open?id={ID}", "Google Drive link"),
    (f"https://drive.google.com/uc?id={ID}&export=download", "Google Drive link"),
    (f"https://drive.google.com/u/0/open?id={ID}", "Google Drive link"),
    (f"docs.google.com/document/d/{ID}", "Google Doc link"),
    (f"HTTP://DOCS.GOOGLE.COM/document/d/{ID}/edit", "Google Doc link"),
])
def test_each_google_url_form_is_recognised_with_its_label(url, label):
    ref = recognise(url)
    assert ref is not None
    assert (ref.provider, ref.file_id, ref.url, ref.label) == ("google_drive", ID, url, label)
    assert ref.handle == HANDLE


DOC = f"https://docs.google.com/document/d/{ID}/edit"
DRIVE = f"https://drive.google.com/open?id={ID}"


@pytest.mark.parametrize("text", [
    # Glued to what came before it.
    f"link:{DOC}", f"see...{DOC}", f"doc-{DOC}", f"2{DOC}", f"x{DOC}", f"@{DOC}",
    f"_{DOC}_", f"%{DOC}", f"文件：{DOC}", f"睇下{DOC}呀",
    # Chat formatting around it, or glued after it.
    f"*{DOC}*", f"**{DOC}**", f"~{DOC}~", f"~~{DOC}~~", f"`{DOC}`", f"{DOC}'s",
    f"{DOC}=", f"{DOC}+", f"{DOC}...", f"({DOC})", f"<{DOC}>", f"[x]({DOC})",
    f"**{DRIVE}**", f"{DRIVE}/", f"{DRIVE}'s", f"{DRIVE}=", f"_{DRIVE}_",
    # Google's own redirect wrapper, plain and percent-encoded.
    f"https://www.google.com/url?q={DOC}&sa=D",
    "https://www.google.com/url?q=https%3A%2F%2Fdocs.google.com%2Fdocument%2Fd%2F"
    f"{ID}%2Fedit&sa=D",
    # Legacy Workspace prefix, upper case, a percent-encoded tail, other id forms.
    f"https://docs.google.com/a/example.com/document/d/{ID}/edit",
    f"https://drive.google.com/a/example.com/file/d/{ID}/view",
    f"HTTPS://DOCS.GOOGLE.COM/DOCUMENT/D/{ID}/EDIT",
    f"https://docs.google.com/document/d/{ID}%2Fedit",
    f"https://docs.google.com/uc?id={ID}",
    f"https://drive.usercontent.google.com/download?id={ID}&export=download",
    # A file link INSIDE another URL: the whole wrapper
    # becomes the handle.
    f"https://example.com/?next={DOC}",
    f"https://example.com/{DOC}",
    f"example.com/?next={DOC}",
    "https://example.com/?u=https%3A%2F%2Fdocs.google.com%2Fdocument%2Fd%2F" + ID,
    "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fdocs.google.com"
    f"%2Fdocument%2Fd%2F{ID}%2Fedit&data=05%7C02&reserved=0",
    f"https://l.facebook.com/l.php?u=https%3A%2F%2Fdrive.google.com%2Fopen%3Fid%3D{ID}&h=AT0",
    f"https://www.google.com/url?q=https://www.google.com/url?q={DOC}",
    f"[x](https://www.google.com/url?q={DOC})",
])
def test_the_link_never_leaks_however_it_is_written(text):
    """Each of these must not reach a model as the raw URL. The
    file id must not survive the render, and the ref must be recorded."""
    rendered = render_file_links(text)
    assert ID not in rendered and "google.com" not in rendered.lower(), rendered
    assert [r.file_id for r in file_refs_in(text)] == [ID]


ID_UNDERSCORE = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123_"


@pytest.mark.parametrize("text", [
    f"https://docs.google.com/document/d/{ID_UNDERSCORE}",
    f"https://drive.google.com/open?id={ID_UNDERSCORE}",
    f"see https://docs.google.com/document/d/{ID_UNDERSCORE}.",
    f"https://docs.google.com/document/d/{ID_UNDERSCORE}/edit",
])
def test_an_id_ending_in_an_underscore_keeps_it(text):
    """Drive ids are base64url: a trailing `_` is part of the id unless the
    link also OPENED with one (`_url_` emphasis, tested above)."""
    want = hashlib.sha256(f"google_drive:{ID_UNDERSCORE}".encode()).hexdigest()[:6]
    assert [r.file_id for r in file_refs_in(text)] == [ID_UNDERSCORE]
    rendered = render_file_links(text)
    assert want in rendered and not rendered.split("]")[-1].startswith("_")


def test_emphasis_underscores_are_not_part_of_the_id():
    assert [r.file_id for r in file_refs_in(f"_{DRIVE}_")] == [ID]
    assert render_file_links(f"_{DRIVE}_") == f"_[Google Drive link {HANDLE}]_"


def test_two_links_glued_together_are_two_links():
    text = f"{DOC}https://drive.google.com/file/d/{OTHER}/view"
    assert [r.file_id for r in file_refs_in(text)] == [ID, OTHER]
    rendered = render_file_links(text)
    assert ID not in rendered and OTHER not in rendered
    assert rendered.count("[Google ") == 2


def test_a_wrapper_keeps_its_posted_url_and_names_the_inner_file():
    wrapped = f"https://example.com/?next={DOC}"
    (ref,) = file_refs_in(f"see {wrapped} ok")
    assert (ref.file_id, ref.url, ref.label) == (ID, wrapped, "Google Doc link")


def test_the_resource_key_is_kept():
    ref = recognise(f"https://drive.google.com/file/d/{ID}/view?resourcekey=0-AbC_dE")
    assert ref is not None and ref.resource_key == "0-AbC_dE"
    assert recognise(f"https://docs.google.com/document/d/{ID}/edit").resource_key is None


@pytest.mark.parametrize("url", [
    # A folder is not a file: it stays an ordinary link.
    f"https://drive.google.com/drive/folders/{ID}",
    f"https://drive.google.com/drive/u/0/folders/{ID}?usp=sharing",
    # Lookalike hosts: exact match only.
    f"https://docs.google.com.evil.example/document/d/{ID}/edit",
    f"https://evil-docs.google.com/document/d/{ID}/edit",
    f"https://docs.google.co/document/d/{ID}/edit",
    f"https://docs.google.com./document/d/{ID}/edit",
    f"https://drive.google.com.example.net/file/d/{ID}/view",
    f"https://google.com/document/d/{ID}",
    # Credentials, a port, another scheme.
    f"https://user@docs.google.com/document/d/{ID}/edit",
    f"https://docs.google.com:8443/document/d/{ID}/edit",
    f"ftp://docs.google.com/document/d/{ID}/edit",
    # Not a file id: too short, a published-to-web link, bad characters.
    "https://docs.google.com/document/d/short/edit",
    f"https://docs.google.com/document/d/e/2PACX-{ID}/pub",
    f"https://docs.google.com/forms/d/{ID}/viewform",
    "https://drive.google.com/open?id=a%20b",
    f"https://drive.google.com/open?id={ID}&id={OTHER}",
    # Not real Google URL shapes.
    f"https://www.drive.google.com/file/d/{ID}",
    f"https://www.google.com/url?q=https://example.com/d/{ID}",
    "https://example.com/somewhere",
    "not a url",
])
def test_anything_else_is_not_recognised(url):
    assert recognise(url) is None


def test_the_render_replaces_the_url_in_place_and_keeps_everything_else():
    text = (
        f"睇下https://docs.google.com/document/d/{ID}/edit?usp=sharing呀, "
        f"and (https://docs.google.com/spreadsheets/d/{OTHER}/edit). "
        f"folder https://drive.google.com/drive/folders/{ID} "
        f"<https://drive.google.com/open?id={ID}> https://example.com/x"
    )
    other = hashlib.sha256(f"google_drive:{OTHER}".encode()).hexdigest()[:6]
    assert render_file_links(text) == (
        f"睇下[Google Doc link {HANDLE}]呀, "
        f"and ([Google Sheet link {other}]). "
        f"folder https://drive.google.com/drive/folders/{ID} "
        f"<[Google Drive link {HANDLE}]> https://example.com/x"
    )


def test_the_render_never_says_file():
    rendered = render_file_links(f"https://drive.google.com/file/d/{ID}/view")
    assert "file" not in rendered.lower() and ID not in rendered


def test_text_without_a_file_link_is_returned_unchanged():
    for text in ("", "hello", "https://www.google.com/search?q=x", "[image a3f2c1]"):
        assert render_file_links(text) == text


def test_the_handle_parsers_read_the_multi_word_labels():
    rendered = render_file_links(f"see https://docs.google.com/presentation/d/{ID}/edit")
    assert link_handles_in(rendered) == (("Google Slides link", HANDLE),)
    assert handle_hex(f"[Google Slides link {HANDLE}]") == HANDLE


def test_refs_are_one_per_file_in_order_and_a_later_resource_key_fills_in():
    text = (
        f"https://docs.google.com/document/d/{ID}/edit then "
        f"https://docs.google.com/document/d/{OTHER}/edit then "
        f"https://drive.google.com/file/d/{ID}/view?resourcekey=0-k"
    )
    refs = file_refs_in(text)
    assert [r.file_id for r in refs] == [ID, OTHER]
    assert refs[0].resource_key == "0-k" and refs[0].label == "Google Doc link"


def test_the_stored_form_round_trips_and_is_null_for_none():
    refs = file_refs_in(
        f"https://drive.google.com/open?id={ID}&resourcekey=0-k "
        f"https://docs.google.com/spreadsheets/d/{OTHER}/edit"
    )
    assert external_files_json(()) is None
    assert refs_from_external_files_json(external_files_json(refs)) == refs
    for bad in (None, "", "not json", "{}", '[{"provider": 1}]'):
        assert refs_from_external_files_json(bad) == ()


def _msg(body: str, *, conv: str = "g1", ts: int) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conv, conversation_type="group",
        sender_id="+85200000000", sender_name="A", timestamp=ts, body=body, raw={},
        external_files=file_refs_in(body),
    )


async def _store(*bodies: tuple[str, str, int]) -> FakeRecordStore:
    store = FakeRecordStore()
    for conv, body, ts in bodies:
        await store.append(message=_msg(body, conv=conv, ts=ts))
    return store


NOW = 10 * 86_400_000


async def test_a_code_resolves_to_one_file_in_this_room():
    store = await _store(
        ("g1", f"https://docs.google.com/document/d/{ID}/edit", NOW - 1000),
        ("g1", f"https://drive.google.com/open?id={ID}", NOW - 500),
    )
    found = await resolve_file_code(
        store, code=f"[Google Doc link {HANDLE}]", conversation_id="g1", now_ms=NOW,
    )
    assert found.status == "ok"
    assert [r.file_id for r in found.refs] == [ID]
    # Newest posting first: the Drive form posted last is the ref kept.
    assert found.refs[0].label == "Google Drive link"


async def test_a_code_from_another_room_or_nowhere_is_nothing_found():
    store = await _store(("g2", f"https://docs.google.com/document/d/{ID}/edit", NOW - 1))
    for code in (HANDLE, "abcdef", "ab"):
        found = await resolve_file_code(store, code=code, conversation_id="g1", now_ms=NOW)
        assert found.status == "nothing_found" and found.refs == ()


def _colliding_ids() -> tuple[str, str]:
    """Two file ids whose handles collide: a birthday search over 24 bits,
    a few thousand hashes."""
    seen: dict[str, str] = {}
    i = 0
    while True:
        fid = f"1Collide{i:012d}xyz"
        h = hashlib.sha256(f"google_drive:{fid}".encode()).hexdigest()[:6]
        if h in seen:
            return seen[h], fid
        seen[h] = fid
        i += 1


async def test_two_files_sharing_a_code_are_ambiguous_never_picked():
    a_id, b_id = _colliding_ids()
    a = ExternalFileRef("google_drive", a_id, "u1", label="Google Doc link")
    b = ExternalFileRef("google_drive", b_id, "u2", label="Google Sheet link")
    assert a.handle == b.handle and a.file_id != b.file_id
    store = FakeRecordStore()
    await store.append(message=dataclasses.replace(_msg("y", ts=NOW - 2), external_files=(a,)))
    await store.append(message=dataclasses.replace(_msg("z", ts=NOW - 3), external_files=(b,)))
    found = await resolve_file_code(store, code=a.handle, conversation_id="g1", now_ms=NOW)
    assert found.status == "ambiguous"
    assert {r.file_id for r in found.refs} == {a_id, b_id}


async def test_an_unreadable_record_is_unavailable_not_nothing_found():
    store = await _store(("g1", f"https://docs.google.com/document/d/{ID}/edit", NOW - 1))
    store.fail_on_recent = True
    found = await resolve_file_code(store, code=HANDLE, conversation_id="g1", now_ms=NOW)
    assert found.status == "unavailable"


@pytest.mark.parametrize(("text", "want"), [
    ("https://drive.google.com/file/d/491db6", "491db6"),
    ("drive.google.com/drive/folders/491DB6", "491db6"),
    ("https://docs.google.com/spreadsheets/d/98b65b/edit", "98b65b"),
    ("https://drive.google.com/open?id=491db6", "491db6"),
    # A real file id is long: that link is the file, never a code.
    ("https://docs.google.com/document/d/1SynthDocLink000000000000/edit", None),
    ("https://example.com/file/d/491db6", None),            # not a provider host
    ("https://drive.google.com/file/d/491db6?id=98b65b", None),  # two codes: no guess
    ("https://drive.google.com/file/d/491db6/98b65b", "491db6"),  # only the id slot
    ("https://drive.google.com/file/d/49x1db", None),
    # A six-hex value outside the id slot is the link's.
    ("https://docs.google.com:443/spreadsheets/d/1SynthDocLink000000000000/edit?gid=123456", None),
    ("https://drive.google.com/drive/folders/1SynthDocLink000000000000?sort=123456", None),
    ("https://docs.google.com/forms/d/e/1SynthDocLink000000000000/viewform?entry.1=decade", None),
    ("https://drive.google.com:99/file/d/491db6", None),
    ("ftp://drive.google.com/file/d/491db6", None),
])
def test_a_code_inside_a_link_built_around_it_is_the_code(text, want):
    from nora.domain.external_files import quoted_handle_code
    assert quoted_handle_code(text) == want
