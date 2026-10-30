"""The identifier detector's rules, each against GENERATED values.

Every value here is built at run time from a seeded generator
(`tests/pii_synth.py`'s `Synth`, plus invented names); none is copied
from a record, and this file holds no identifier-shaped literal of its own, so
the detector passes it like any other file. Each rule is checked on the form
it must catch AND on the look-alike it must not.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

from tests.pii_synth import Synth

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("pii_scan")
B = _load("pii_denylist_build")

SY = Synth(9001)
_R = random.Random(9001)
_SYL = ["qor", "vel", "tam", "ris", "pal", "dra", "ken", "mor", "zul", "wix", "bran", "sto"]


def _invented_word() -> str:
    return "".join(_R.choice(_SYL) for _ in range(3)).capitalize()


GIVEN, FAMILY = _invented_word(), _invented_word()
NAME = f"{GIVEN} {FAMILY}"
PHONE = SY.phone("44")
NSN = PHONE[3:]  # national significant number
UUID = SY.uuid()
GID = SY.signal_group()
CJK = "鵺鶲鸊"


def _deny():
    return P.parse_denylist(
        f"text\t{NAME}\nphone\t{PHONE}\nuuid\t{UUID}\nb64\t{GID}\ntext\t{CJK}\n", "test"
    )


def _scanner(allow=None, placeholders=None):
    return P.Scanner(_deny(), allow or P.Allowlist(), placeholders or set())


def _rules(text: str, sc=None) -> list[str]:
    return [f.rule for f in (sc or _scanner()).scan_text(text, "f.py").findings]


def _fullwidth(s: str) -> str:
    return s.translate({ord(c): ord(c) + 0xFEE0 for c in "0123456789"})


# ---- denylist: one real value, every spelling


@pytest.mark.parametrize(
    "form",
    [
        NAME,
        NAME.upper(),
        NAME.lower(),
        f"{GIVEN.lower()}_{FAMILY.lower()}",
        f"{GIVEN.lower()}{FAMILY}",
        f"{GIVEN}{FAMILY}".lower(),
        f"test_{GIVEN.lower()}_{FAMILY.lower()}_x",
        f'"hi\\n{NAME}: ok"',
        f"{GIVEN}-{FAMILY}",
    ],
)
def test_denylist_name_every_spelling(form):
    assert "denylist:text" in _rules(f"x = {form!r}\n")


def test_denylist_name_needs_every_token():
    assert _rules(f"hello {GIVEN} and {_invented_word()} {FAMILY}\n") == []


@pytest.mark.parametrize(
    "form",
    [
        PHONE,
        f"+44 {NSN[:4]} {NSN[4:]}",
        f"+44-{NSN[:4]}-{NSN[4:]}",
        f"+44.{NSN[:4]}.{NSN[4:]}",
        f"0{NSN}",
        f"(0{NSN[:4]}) {NSN[4:]}",
        f"44{NSN}@s.whatsapp.net",
        _fullwidth(PHONE),
        PHONE[:6] + "\u200b" + PHONE[6:],
    ],
)
def test_denylist_phone_every_spelling(form):
    assert "denylist:number" in _rules(f'sender = "{form}"\n')


@pytest.mark.parametrize(
    "form",
    [
        UUID,
        UUID.upper(),
        UUID.replace("-", ""),
        UUID.replace("-", "").upper(),
        f"id={UUID[:8]} short",
    ],
)
def test_denylist_uuid_dashless_upper_and_prefix(form):
    assert "denylist:uuid" in _rules(f"{form}\n")


@pytest.mark.parametrize(
    "form",
    [
        GID,
        GID.replace("+", "-").replace("/", "_").rstrip("="),
        GID.replace("+", "%2B").replace("/", "%2F").replace("=", "%3D"),
    ],
)
def test_denylist_signal_group_every_encoding(form):
    assert "denylist:b64" in _rules(f"group={form}\n")


def test_denylist_cjk_is_a_substring_match():
    assert "denylist:text" in _rules(f"佢叫{CJK}呀\n")


_ORG = "mikeng-io"
PROJECT_TOKENS = {_ORG, _ORG.removesuffix("-io"), "mikeng.io", "iam@mikeng.io"}


def test_project_identity_is_the_org_and_the_domain_only():
    """Project identity in code is the public org/domain tokens and nothing
    else. A personal name in it would be rewritten by the history rewrite -- the
    hook's own source would change under it -- and would exempt a person from the
    denylist."""
    assert set(P.PROJECT_IDENTITY) == PROJECT_TOKENS
    assert set(B.PROJECT_IDENTITY) == PROJECT_TOKENS
    for v in P.PROJECT_IDENTITY:
        assert " " not in v


def test_project_identity_is_never_denied_and_a_personal_name_always_is():
    d = P.parse_denylist(
        "".join(
            f"text\t{v}\n"
            for v in (
                _ORG, _ORG.replace("-", "_"), _ORG.removesuffix("-io"), "iam@mikeng.io", NAME
            )
        ),
        "t",
    )
    assert d.count == 1
    sc = P.Scanner(d, P.Allowlist(), set())
    line = f"Copyright {NAME} <iam@mikeng.io>, github.com/mikeng-io\n"
    assert [f.rule for f in sc.scan_text(line, "LICENSE").findings] == ["denylist:text"]
    project_only = "Copyright <iam@mikeng.io>, github.com/mikeng-io\n"
    assert sc.scan_text(project_only, "LICENSE").findings == []
    owner = f"{GIVEN.lower()}_{FAMILY.lower()}"
    assert P.parse_denylist(f"text\t{owner}\n", "t").count == 1


def test_an_owner_name_that_spells_the_org_stem_never_fires_inside_the_org():
    """The owner's name, written with a separator, is denied (the test
    above); with the separator gone it is the org's stem, so separator-free
    matching would find it inside `mikeng-io`, `mikeng.io` and the project email
    and refuse repository URLs. Project identity is blanked
    before matching, like a keep-phrase; the name on its own still fires."""
    stem = _ORG.removesuffix("-io")
    owner = f"{stem[:4].title()} {stem[4:].title()}"
    d = P.parse_denylist(f"text\t{owner}\n", "t")
    assert d.count == 1
    sc = P.Scanner(d, P.Allowlist(), set())
    for project in (
        f"see github.com/{_ORG}/nora\n",
        f"branch {_ORG}/readme-note\n",
        "mail iam@mikeng.io or visit mikeng.io\n",
    ):
        assert sc.scan_text(project, "README.md").findings == [], project
    # the bare stem is a handle, another mailbox at the domain is a person
    for personal in (
        f"github.com/{stem}\n",
        f"ping @{stem}\n",
        f"write to x@{stem}.io\n",
        f"{stem}.dev\n",
    ):
        assert [f.rule for f in sc.scan_text(personal, "x.md").findings
                if f.rule == "denylist:text"] == ["denylist:text"], personal
    assert [f.rule for f in sc.scan_text(f"signed, {owner}\n", "x.md").findings] == [
        "denylist:text"
    ]
    assert [f.rule for f in sc.scan_text(f"{stem}FBID 1\n", "x.md").findings] == [
        "denylist:text"
    ]


def test_denylist_rejects_unknown_kind():
    with pytest.raises(P.DenylistError):
        P.parse_denylist("secret\tx\n", "t")


# ---- shapes


@pytest.mark.parametrize("cc", ["44", "81", "1", "852", "65", "61", "49", "91"])
def test_phone_shape_any_country(cc):
    assert _rules(f'contact = "{SY.phone(cc, "spaced")}"\n') == ["phone"]
    assert _rules(f'contact = "{SY.phone(cc, "dashed")}"\n') == ["phone"]
    assert _rules(f'contact = "{SY.phone(cc)}"\n') == ["phone"]


@pytest.mark.parametrize(
    "text",
    [
        "released 2026-09-26 at 10:30",
        "copyright 1999-2026",
        "open 0900-1800 daily",
        "host 10.0.0.12 up",
        'phone = "+1 555 0100"',
        "signal timeout 2026-01-02",
        'phone = "+852 1234 5678"',
    ],
)
def test_phone_shape_skips_dates_times_years_placeholders(text):
    assert _rules(text + "\n") == []


def test_bare_digits_are_a_phone_only_where_the_line_says_so():
    local = SY.phone("852", "local")
    assert _rules(f"whatsapp {local}\n") == ["phone"]
    assert _rules(f"row {local}\n") == []


def test_email_shape_and_its_placeholders():
    assert _rules(f"to: {SY.email()}\n") == ["email"]
    for addr in ("someone@example.com", "noreply@anthropic.com", "iam@mikeng.io", "x@host.test"):
        assert _rules(f"to: {addr}\n") == [], addr


def test_snowflake_uuid_jid_group_tailscale_shapes():
    assert _rules(f'"id": "{SY.snowflake()}"\n') == ["snowflake"]
    assert _rules(f"uid {SY.uuid()}\n") == ["uuid"]
    assert _rules(f"{SY.phone('91', 'jid')}\n") == ["whatsapp-jid"]
    assert _rules(f'"groupId": "{SY.signal_group()}"\n') == ["signal-group"]
    host = f"{_invented_word().lower()}.tail{SY.uuid()[:4]}.ts.net"
    assert _rules(f"ssh {host}\n") == ["tailscale"]
    assert _rules(
        f"peer 100.{_R.randint(64, 127)}.{_R.randint(1, 254)}.{_R.randint(1, 254)}\n"
    ) == ["tailscale-ip"]


def test_placeholder_ids_pass():
    zero_uuid = "-".join(["a1000001", "0000", "4000", "8000", "000000000001"])
    assert _rules(f"uid {zero_uuid}\n") == []
    assert _rules(f"id {'1' + '0' * 16 + '1'}\n") == []
    assert _rules(f"id {SY.snowflake()[:6] + '0' * 5 + SY.snowflake()[:7]}\n") == []
    assert _rules("h1:" + SY.signal_group() + "\n") == []  # a Go checksum, not a group


def test_identity_example_values_are_placeholders():
    local = SY.phone("852")
    ph = P.placeholder_values(f'[[persons.x.signal]]\nphone = "{local}"\n')
    assert _rules(f'phone = "{local}"\n', _scanner(placeholders=ph)) == []
    assert _rules(f'phone = "{local}"\n') == ["phone"]


def test_value_allowlist_exempts_a_shape_but_never_a_denylist_hit():
    other = SY.phone("44")
    near = P.digits_of(other)[:10] + r"\d\d"  # a narrow range around `other`
    allow = P.parse_allowlist(f"value {P.digits_of(PHONE)} -- test\nvalue-re {near} -- test\n")
    assert _rules(f'p = "{other}"\n', _scanner(allow=allow)) == []
    assert "denylist:number" in _rules(f'p = "{PHONE}"\n', _scanner(allow=allow))


# ---- coordinates: fields, rows x places


def _rows(points, fmt="{:.6f}, {:.6f}"):
    return [f"    ({fmt.format(la, ln)}, {i})," for i, (la, ln) in enumerate(points)]


def test_coords_three_fixes_in_one_place_fail():
    base = SY.base_point()
    assert P.scan_coords(_rows([SY.near(base) for _ in range(3)])).over


def test_coords_small_honest_fixture_passes():
    two = [SY.near(SY.base_point()) for _ in range(2)]
    places = [SY.base_point() for _ in range(4)]
    assert not P.scan_coords(_rows(two + two[:0])).over
    assert not P.scan_coords(_rows(places)).over


def test_coords_2000_rows_two_places_fail():
    a, b = SY.base_point(), SY.base_point()
    res = P.scan_coords(_rows([SY.near(a if i % 2 else b) for i in range(2000)]))
    assert res.over and res.clusters == 2 and res.rows == 2000


def test_coords_two_fixes_at_three_places_fail():
    """No single place reaches three fixes; the dwell score still adds up."""
    pts = []
    for _ in range(3):
        base = SY.base_point()
        pts += [SY.near(base), SY.near(base)]
    res = P.scan_coords(_rows(pts))
    assert res.max_rows == 2 and res.over


def test_coords_int_microdegrees_fail():
    base = SY.base_point()
    rows = ["lat_e6,lng_e6,ts"] + [
        f"{int(la * 1e6)},{int(ln * 1e6)},{i}"
        for i, (la, ln) in enumerate(SY.near(base) for _ in range(5))
    ]
    assert P.scan_coords(rows).over


def test_explicit_microdegree_csv_header_covers_subdegree_values():
    synth = Synth(1)
    raw = synth.base_point()
    base = (raw[0]/1000, raw[1]/1000)
    rows = ["lat_e6,lng_e6,ts"] + [
        f"{int(la*1e6)},{int(ln*1e6)},{i}"
        for i, (la,ln) in enumerate(synth.near(base) for _ in range(5))
    ]
    assert P.scan_coords(rows).over
    assert not P.scan_coords(rows[1:]).over
    assert not P.scan_coords([rows[0], "code = [1, 2, 3]"]).points


def test_coords_int_microdegrees_need_a_geo_word():
    """The declared gap: a headerless integer table reads as ids."""
    base = SY.base_point()
    rows = [f"{int(la * 1e6)},{int(ln * 1e6)}" for la, ln in (SY.near(base) for _ in range(5))]
    assert not P.scan_coords(rows).over


def test_coords_comma_decimal_and_split_columns_fail():
    base = SY.base_point()
    pts = [SY.near(base) for _ in range(4)]
    comma = [f"{la:.6f};{ln:.6f}".replace(".", ",") for la, ln in pts]
    split = [f"{la:.6f},{ln:.6f}".replace(".", ",") for la, ln in pts]
    assert P.scan_coords(comma).over
    assert P.scan_coords(split).over


def test_coords_json_with_a_key_between_and_across_lines_fail():
    base = SY.base_point()
    inline = [
        f'{{"lat": {la:.5f}, "ts": {1000 + i}, "lng": {ln:.5f}}}'
        for i, (la, ln) in enumerate(SY.near(base) for _ in range(3))
    ]
    assert P.scan_coords(inline).over
    pretty = []
    for la, ln in (SY.near(base) for _ in range(3)):
        pretty += ["  {", f'    "lat": {la:.5f},', f'    "lng": {ln:.5f}', "  },"]
    assert P.scan_coords(pretty).over


def test_coords_dms_and_plus_codes_fail():
    dms = [f"22°18'{10 + i:02d}\"N 114°10'{20 + i:02d}\"E" for i in range(3)]
    assert P.scan_coords(dms).over
    code = "".join(_R.choice("23456789CFGHJMPQRVWX") for _ in range(8))
    assert P.scan_coords([f"{code}+{x}" for x in ("2C", "2F", "3C")]).over


def test_coords_versions_metrics_and_clock_times_pass():
    lines = [f'"node": "^{_R.randint(10, 30)}.{_R.randint(10, 20)}.0"' for _ in range(40)]
    level = _R.uniform(10, 50)  # a converging metric: neighbouring values sit together
    lines += ["[" + ", ".join(f"{level + _R.uniform(0, 1e-5):.15f}" for _ in range(40)) + "]"]
    lines += [
        f"at 12:{_R.randint(10, 59)}:{_R.randint(10, 59)}.{_R.randint(100, 999)} ok"
        for _ in range(40)
    ]
    assert not P.scan_coords(lines).over


# ---- media metadata


def _jpeg_with_gps(gps: bool) -> bytes:
    import struct

    tiff = bytearray(b"II*\x00" + struct.pack("<I", 8))
    entries = [(0x8825, 4, 1, 26)] if gps else [(0x0110, 2, 1, 0)]
    tiff += struct.pack("<H", len(entries))
    for tag, typ, cnt, val in entries:
        tiff += struct.pack("<HHII", tag, typ, cnt, val)
    tiff += struct.pack("<I", 0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 2, 5, 3, 0) + struct.pack("<I", 0)
    app1 = b"Exif\x00\x00" + bytes(tiff)
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


def test_media_gps_in_exif_and_quicktime():
    assert P.media_has_gps(_jpeg_with_gps(True))
    assert not P.media_has_gps(_jpeg_with_gps(False))
    la, ln = SY.base_point()
    assert P.media_has_gps(
        b"\x00moov" + b"\xa9xyz\x00\x12\x15\xc7" + f"{la:+08.4f}{ln:+09.4f}/".encode()
    )


# ---- a blob, judged against its parent


def _job(new: bytes, *olds, path="tests/fixtures/x.py", mode="100644"):
    return P.BlobJob(path, mode, new, list(olds) or [None])


def _judge(job, allow=None, acks=()):
    return P.judge_blob(job, _scanner(allow), allow or P.Allowlist(), set(acks)).findings


def test_existing_value_is_not_new_and_an_added_one_is():
    old = f'a = "{SY.email()}"\n'.encode()
    assert _judge(_job(old + b"b = 1\n", old)) == []
    new = old + f'c = "{SY.email()}"\n'.encode()
    fs = _judge(_job(new, old))
    assert [(f.rule, f.line) for f in fs] == [("email", 2)]


def test_editing_a_line_that_already_held_a_value_is_not_new():
    addr = SY.email()
    old = f'a = "{addr}"  # first\n'.encode()
    assert _judge(_job(f'a = "{addr}"  # edited\n'.encode(), old)) == []


def test_utf16_and_a_trailing_non_utf8_byte_are_errors_not_skips():
    """An ERROR, never a skip -- and the denylist hit in what can still be
    read is reported beside it."""
    payload = f"x = {NAME!r}\n"
    hit = ("FAIL", "denylist:text")
    fs = _judge(_job(payload.encode("utf-16")))
    assert {(f.status, f.rule) for f in fs} == {("ERROR", "utf16"), hit}
    fs = _judge(_job(f"{CJK}\n".encode("utf-16")))
    assert {(f.status, f.rule) for f in fs} == {("ERROR", "utf16"), hit}
    fs = _judge(_job(payload.encode() + b"\xff"))
    assert {(f.status, f.rule) for f in fs} == {("ERROR", "undecodable"), hit}
    fs = _judge(_job(b"x = 1\n" + b"\xff"))
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable")]


def test_declared_binary_is_scanned_for_strings_and_media_gps():
    allow = P.parse_allowlist("binary media/art/* -- test\n")
    fs = _judge(
        _job(b"\x00\x01PNG" + f"Author: {NAME} ".encode() + b"\x00", path="media/art/a.png"), allow
    )
    assert "denylist:text" in [f.rule for f in fs]
    fs = _judge(_job(_jpeg_with_gps(True), path="media/art/b.jpg"), allow)
    assert [f.rule for f in fs] == ["media-gps"]


def test_symlink_gitlink_and_oversize_are_errors_unless_declared():
    assert [f.rule for f in _judge(_job(b"../../x", mode="120000"))] == ["symlink"]
    assert [f.rule for f in _judge(P.BlobJob("vendor/sub", "160000", None, [None]))] == ["gitlink"]
    big = b"a = 1\n" * (P.MAX_BYTES // 6 + 10)
    assert [f.rule for f in _judge(_job(big))] == ["too-large"]
    allow = P.parse_allowlist("symlink tests/fixtures/* -- test\n")
    assert _judge(_job(b"../../x", mode="120000"), allow) == []


def test_swapped_coordinates_fail_where_a_size_pin_would_not():
    base = SY.base_point()
    old_rows = "\n".join(_rows([SY.near(base) for _ in range(5)])).encode()
    old_digest = P.scan_coords(old_rows.decode().split("\n")).digest()
    allow = P.parse_allowlist(f"coords tests/fixtures/x.py sha256={old_digest} -- test\n")
    assert _judge(_job(old_rows, None), allow) == []  # the declared table itself
    other = SY.base_point()
    swapped = "\n".join(_rows([SY.near(other) for _ in range(5)])).encode()
    fs = _judge(_job(swapped, old_rows), allow)
    assert [f.rule for f in fs] == ["coords"]  # same size, different content


def test_a_merge_adds_only_what_no_parent_had():
    a = f'x = "{SY.email()}"\n'.encode()
    b = f'y = "{SY.email()}"\n'.encode()
    assert _judge(_job(a + b, a, b)) == []
    assert [f.rule for f in _judge(_job(a + b, a, b"z = 0\n"))] == ["email"]


# ---- acknowledgement, ids, masking


def test_ack_clears_a_shape_but_never_a_denylist_hit():
    text = f'p = "{SY.phone("44")}"\nq = {NAME!r}\n'
    fs = _scanner().scan_text(text, "f.py").findings
    ids = {f.fid for f in fs}
    used = P.apply_acks(fs, ids)
    assert {f.rule: f.state for f in fs} == {"phone": "ACKED", "denylist:text": ""}
    assert used == {f.fid for f in fs if f.rule == "phone"}


def test_finding_id_is_bound_to_path_and_line():
    assert P.finding_id("a.py", "x = 1") == P.finding_id("a.py", "x = 1   ")
    assert P.finding_id("a.py", "x = 1") != P.finding_id("b.py", "x = 1")
    assert P.finding_id("a.py", "x = 1") != P.finding_id("a.py", "x = 2")


def test_output_never_prints_a_value(capsys):
    other = SY.phone("81")
    rep = P.Report(
        findings=_scanner()
        .scan_text(f'p = "{other}"\nq = {NAME!r}\nr = "{PHONE}"\n', "f.py")
        .findings
    )
    assert P.emit(rep, "staged", 0.0) == P.FAIL
    err = capsys.readouterr().err
    for secret in (NAME, GIVEN, FAMILY, other, P.digits_of(other), PHONE, NSN):
        assert secret not in err


# ---- the denylist and the declarations fail closed


def test_missing_denylist_is_an_error_unless_acknowledged(monkeypatch, tmp_path):
    monkeypatch.setenv("NORA_PII_DENYLIST", str(tmp_path / "none.txt"))
    monkeypatch.delenv("NORA_PII_DENYLIST_ABSENT", raising=False)
    deny, err, _ = P.load_denylist()
    assert deny is None and err.rule == "denylist-missing" and err.status == "ERROR"
    monkeypatch.setenv("NORA_PII_DENYLIST_ABSENT", "fresh CI runner")
    deny, err, notice = P.load_denylist()
    assert err is None and deny is not None and "fresh CI runner" in notice


def test_empty_denylist_is_an_error(monkeypatch, tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("# header only\n")
    monkeypatch.setenv("NORA_PII_DENYLIST", str(f))
    assert P.load_denylist()[1].rule == "denylist-empty"


@pytest.mark.parametrize(
    "line",
    [
        "binary assets/*",
        "binary assets/* -- ",
        "skip assets/* -- why",
        "coords a.py -- why",
        "coords a.py sha256=xyz -- why",
        "value-re ( -- why",
    ],
)
def test_malformed_declaration_is_an_error(line):
    with pytest.raises(P.AllowlistError):
        P.parse_allowlist(line + "\n")


def test_the_whole_message_file_is_scanned_comments_and_scissors_included(tmp_path, monkeypatch):
    """`git commit -m` and --cleanup=verbatim keep `#` lines and text past the
    scissors, so neither may be skipped."""
    scissors = "# " + "-" * 24 + " >8 " + "-" * 24
    for body in (f"feat: x\n\n# {NAME}\n", f"feat: x\n\n{scissors}\n{NAME}\n"):
        msg = tmp_path / "MSG"
        msg.write_text(body)
        monkeypatch.setattr(P, "rev_exists", lambda _r: None)
        monkeypatch.setattr(P, "git", lambda *a, **k: b"")
        rep = P.mode_message(_deny(), str(msg))
        assert "denylist:text" in [f.rule for f in rep.findings], body


# ---- acknowledgement trailers and crashes


def test_trailer_acks_honour_a_human_claim_and_never_an_agent_verdict():
    """There is no model adjudicator, so an `(agent ...)` trailer has nothing
    deterministic behind it and clears nothing."""
    msg = (
        "feat: x\n\nPII-Ack: 0123456789ab\nPII-Ack: 1a2b3c4d5e6f (human)\n"
        "PII-Ack: ba9876543210 (agent sonnet: fake, generated)\n"
    )
    assert P.trailer_acks(msg) == {"0123456789ab", "1a2b3c4d5e6f"}


def test_a_crash_is_an_error(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(P, "main", boom)
    assert P._main_fail_closed(["pii_scan.py", "staged"]) == P.ERROR
    assert "nothing was judged" in capsys.readouterr().err


def test_coords_a_constant_offset_is_still_a_table():
    """Shifting every point by one delta moves the table; it does not hide it."""
    base = SY.base_point()
    dla, dln = _R.uniform(0.01, 0.2), _R.uniform(0.01, 0.2)
    shifted = [(la + dla, ln + dln) for la, ln in (SY.near(base) for _ in range(4))]
    assert P.scan_coords(_rows(shifted)).over


# ---- spellings of a name beyond the plain one


def _pct(s: str) -> str:
    return "".join(f"%{b:02X}" for b in s.encode())


def _b64url(s: str) -> str:
    import base64

    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "form",
    [
        "".join(f"\\u{ord(c):04x}" for c in NAME),  # JSON escapes
        f"q?n={GIVEN}%20{FAMILY}",  # URL-encoded space
        f"q?n={_pct(NAME)}",  # every byte percent-encoded
        "".join(f"&#{ord(c)};" for c in NAME),  # HTML numeric entities
        f"token: {_b64url('{')}.{_b64url(json.dumps({'n': NAME}))}.sig",  # a JWT payload
        f"{FAMILY}, {GIVEN}",  # family name first
        f"{GIVEN} Q. {FAMILY}",  # a middle initial
        f"{GIVEN} {FAMILY}".replace("o", "\u043e").replace("a", "\u0430"),  # Cyrillic look-alikes
    ],
)
def test_denylisted_name_in_every_encoding(form):
    assert "denylist:text" in _rules(f'x = "{form}"\n'), form


def _fold_name() -> tuple[str, str]:
    """An invented three-character name whose characters all have a
    different Simplified form: (traditional, simplified)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from pii_cjk_fold import FOLD

    pool = sorted(k for k, v in FOLD.items() if 0x4E00 <= k <= 0x9FFF and len(v) == 1)
    trad = "".join(chr(_R.choice(pool)) for _ in range(3))
    return trad, trad.translate(FOLD)


def test_cjk_name_simplified_and_spaced():
    trad, simp = _fold_name()
    sc = P.Scanner(P.parse_denylist(f"text\t{trad}\n", "t"), P.Allowlist(), set())
    assert trad != simp
    for form in (
        trad,
        simp,
        f"{trad[0]} {trad[1:]}",
        f"{simp[0]}　{simp[1:]}",
        _pct(trad),
        "".join(f"\\u{ord(c):04x}" for c in trad),
    ):
        assert "denylist:text" in _rules(f'"name": "{form}"\n', sc), form


@pytest.mark.parametrize("sep", ["–", "‑", "−", " ", " - ", " "])
def test_denylisted_phone_across_unicode_separators(sep):
    assert "denylist:number" in _rules(f"call +44 {NSN[:4]}{sep}{NSN[4:]}\n")


def test_a_shape_inside_a_denylisted_number_is_not_acknowledgeable():
    """Part of a real number, written so the denylist's own matcher does not
    bridge it, must not become an ack-able phone shape."""
    part = "+" + P.digits_of(PHONE)[:9]
    fs = _scanner().scan_text(f"call {part} please\n", "f.py").findings
    assert [(f.rule, f.ackable) for f in fs] == [("denylist:number", False)]


def test_a_malformed_denylist_line_does_not_echo_its_value():
    with pytest.raises(P.DenylistError) as exc:
        P.parse_denylist(f"{NAME}\ttext\n", "t")
    assert NAME not in str(exc.value) and GIVEN not in str(exc.value)


# ---- cheap phone and id shapes


def test_phone_links_bare_e164_and_worded_local_numbers():
    hk = SY.phone("852")[1:]
    assert _rules(f"https://wa.me/{SY.phone('44')[1:]}\n") == ["phone"]
    assert _rules(f"tel:{SY.phone('81')[1:]}\n") == ["phone"]
    assert _rules(f"from {hk} today\n") == ["phone"]
    assert _rules(f"reach her on {SY.phone('852', 'local')} after 6\n") == ["phone"]


def test_a_dashless_uuid_needs_an_id_word():
    h = SY.uuid().replace("-", "")
    assert _rules(f"member id {h}\n") == ["uuid"]
    assert _rules(f"md5 {h}\n") == []


# ---- coordinates in less obvious shapes


def test_coords_one_fix_labelled_home_fails():
    la, ln = SY.base_point()
    assert P.scan_coords(["[home]", f"lat = {la:.6f}", f"lng = {ln:.6f}"]).over


def test_coords_two_fixes_at_home_and_two_at_work_fail():
    a, b = SY.base_point(), SY.base_point()
    rows = [f"{la:.6f},{ln:.6f}" for la, ln in (SY.near(a), SY.near(a), SY.near(b), SY.near(b))]
    assert P.scan_coords(["lat,lng", *rows]).over


def test_a_table_one_parent_already_had_is_not_new():
    base = SY.base_point()
    table = "lat,lng\n" + "".join(
        f"{la:.6f},{ln:.6f}\n" for la, ln in (SY.near(base) for _ in range(4))
    )
    assert _judge(_job((table + "x = 1\n").encode(), table.encode())) == []
    assert _judge(_job(table.encode(), table.encode(), b"other = 1\n")) == []


def test_a_merge_that_assembles_a_table_fails():
    base = SY.base_point()
    head = "lat,lng\n"
    b1 = head + "".join(f"{la:.6f},{ln:.6f}\n" for la, ln in (SY.near(base), SY.near(base)))
    b2 = head + "".join(f"{la:.6f},{ln:.6f}\n" for la, ln in (SY.near(base), SY.near(base)))
    merged = b1 + b2[len(head) :]
    fs = _judge(_job(merged.encode(), b1.encode(), b2.encode()))
    assert [f.rule for f in fs] == ["coords"]


# ---- binaries


def _png(*chunks: tuple[bytes, bytes]) -> bytes:
    import struct
    import zlib

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    body = b"".join(chunk(t, d) for t, d in chunks)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\0" * 13) + body + chunk(b"IEND", b"")


def _tiff_gps() -> bytes:
    return _jpeg_with_gps(True)[4 + 2 + 6 : -2]  # the TIFF inside the synthetic JPEG's APP1


def test_png_exif_chunk_gps():
    assert P.media_has_gps(_png((b"eXIf", _tiff_gps())))
    assert not P.media_has_gps(_png((b"tEXt", b"Software\0x")))


def test_quicktime_location_key():
    la, ln = SY.base_point()
    key = b"com.apple.quicktime.location.ISO6709"
    assert P.media_has_gps(
        b"\0\0\0\x20ftypqt  " + key + f"\0\0{la:+08.4f}{ln:+09.4f}+010.000/".encode()
    )


def test_compressed_text_chunks_and_archives_are_opened():
    import gzip
    import io
    import zipfile
    import zlib

    allow = P.parse_allowlist("binary media/art/* -- test\n")
    ztxt = _png((b"zTXt", b"Author\0\0" + zlib.compress(NAME.encode())))
    itxt = _png((b"iTXt", b"Author\0\x01\0\0\0" + zlib.compress(NAME.encode())))
    gz = gzip.compress(f"owner: {NAME}\n".encode())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", f"<si><t>{NAME}</t></si>")
    for blob in (ztxt, itxt, gz, buf.getvalue()):
        rules = [f.rule for f in _judge(_job(blob, path="media/art/a.bin"), allow)]
        assert "denylist:text" in rules, blob[:8]


def test_a_decompression_bomb_is_an_error(monkeypatch):
    import gzip

    monkeypatch.setattr(P, "DECODE_MAX", 1000)
    allow = P.parse_allowlist("binary media/art/* -- test\n")
    fs = _judge(_job(gzip.compress(b"a" * 5000), path="media/art/a.gz"), allow)
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable-container")]


# ---- the scanner itself


@pytest.mark.parametrize(
    "unit", ["a", "ab1f", "aB", "1", "a.", "1 ", "+1", "a@", "%4", "&#1", "\\u00"]
)
def test_a_100k_character_line_scans_in_linear_time(unit):
    import time

    line = (unit * (100_000 // len(unit)))[:100_000]
    t0 = time.monotonic()
    _scanner().scan_text(f"x = '{line}'\n", "f.py")
    assert time.monotonic() - t0 < 3.0, unit


@pytest.mark.parametrize(
    "env",
    [{"NORA_PII_MAX_BYTES": "8M"}, {"NORA_PII_PUSH_MAX": "abc"}, {"NORA_PII_DECODE_MAX": "-1"}],
)
def test_bad_configuration_is_an_error_not_a_fail(env, tmp_path):
    import os
    import subprocess

    deny = tmp_path / "d.txt"
    deny.write_text(f"text\t{NAME}\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith("NORA_PII_")}
    e.update(env, NORA_PII_DENYLIST=str(deny))
    p = subprocess.run(  # noqa: S603 - this repo's own script
        [sys.executable, str(ROOT / "scripts" / "pii_scan.py"), "staged"],
        cwd=tmp_path,
        env=e,
        capture_output=True,
        text=True,
    )
    assert p.returncode == P.ERROR and "config" in p.stderr


# ---- directory and file names, archive caps, layered encodings


EMAIL_USER = _invented_word().lower() + "77"
EMAIL = f"{EMAIL_USER}@proton.me"


def _deny2():
    return P.Scanner(
        P.parse_denylist(f"text\t{NAME}\nphone\t{PHONE}\ntext\t{CJK}\ntext\t{EMAIL}\n", "t"),
        P.Allowlist(),
        set(),
    )


@pytest.mark.parametrize(
    "path",
    [
        f"people/{NAME}/notes.md",
        f"people/{GIVEN.lower()}_{FAMILY.lower()}.md",
        f"people/{EMAIL}.txt",
        f"contacts/{PHONE}.vcf",
        f"contacts/+44 {NSN[:4]} {NSN[4:]}.vcf",
    ],
)
def test_a_new_path_is_scanned_and_never_printed(path, capsys):
    """Names, emails and numbers in directory and file names."""
    fs = P.judge_blob(
        P.BlobJob(path, "100644", b"hello\n", [None], True), _deny2(), P.Allowlist(), set()
    ).findings
    assert any(f.rule.startswith("denylist:") for f in fs), path
    P.emit(P.Report(findings=fs), "staged", 0.0)
    err = capsys.readouterr().err
    assert GIVEN not in err and EMAIL_USER not in err and NSN[4:] not in err


def test_an_existing_path_is_masked_but_not_reported_again():
    path = f"people/{NAME}/notes.md"
    rep = P.judge_blob(
        P.BlobJob(path, "100644", b"a = 1\nb = 2\n", [b"a = 1\n"], False),
        _deny2(),
        P.Allowlist(),
        set(),
    )
    assert rep.findings == []
    added = f'a = 1\nc = "{SY.email()}"\n'.encode()
    rep = P.judge_blob(
        P.BlobJob(path, "100644", added, [b"a = 1\n"], False), _deny2(), P.Allowlist(), set()
    )
    assert [f.rule for f in rep.findings] == ["email"]
    assert GIVEN not in rep.findings[0].path and rep.findings[0].path.startswith("<path ")


@pytest.mark.parametrize(
    "pattern,broad",
    [
        ("*", True),
        ("**", True),
        ("*/x.txt", True),
        ("d/*", True),
        ("vendor/*", True),
        ("a/**/b", True),
        ("site/public/*", False),
        ("assets/*.png", False),
        ("scrub/map.txt", False),
        (".claude/skills/*", False),
        ("uv.lock", False),
    ],
)
def test_declaration_breadth(pattern, broad):
    assert P.too_broad(pattern) is broad
    if broad:
        with pytest.raises(P.AllowlistError):
            P.parse_allowlist(f"path-exempt {pattern} -- test\n")


def test_path_exempt_never_exempts_a_denylist_hit():
    allow = P.parse_allowlist("path-exempt vendor/lib/data.txt -- test\n")
    text = f'{NAME}\nphone = "{SY.phone("81")}"\n'.encode()
    fs = P.judge_blob(
        P.BlobJob("vendor/lib/data.txt", "100644", text, [None], True),
        P.Scanner(_deny(), allow, set()),
        allow,
        set(),
    ).findings
    assert [f.rule for f in fs] == ["denylist:text"]


def test_each_finding_on_a_line_has_its_own_id():
    line = f"demo {SY.email()} real {SY.email()} {SY.phone('852', 'spaced')}\n"
    fs = _scanner().scan_text(line, "c.txt").findings
    assert len(fs) == 3 and len({f.fid for f in fs}) == 3
    P.apply_acks(fs, {fs[0].fid})
    assert [f.state for f in fs] == ["ACKED", "", ""]


@pytest.mark.parametrize(
    "form",
    [
        "".join(f"&#x{ord(c):x}" for c in NAME),  # hex entities, no semicolons
        "blob " + NAME.encode().hex(),  # hex bytes
        "".join(c + "́" if c in "aeiou" else c for c in NAME),  # combining accents
    ],
)
def test_more_encodings_of_a_name(form):
    assert "denylist:text" in _rules(f"{form}\n")


def test_cjk_name_with_middle_dots_and_a_spelled_out_email():
    sc = _deny2()
    assert "denylist:text" in _rules("·".join(CJK) + "\n", sc)
    host = EMAIL.split("@")[1].replace(".", " dot ")
    assert "denylist:text" in _rules(f"write to {EMAIL_USER} at {host}\n", sc)


def test_a_denylisted_number_spread_out_with_many_separators():
    assert "denylist:number" in _rules(f"contact +44 {NSN[:4]} ----- {NSN[4:]}\n")


def test_a_long_numeric_line_does_not_assemble_a_number_by_chance():
    digits = P.digits_of(PHONE)
    noise = ", ".join(str(_R.randint(0, 9)) for _ in range(4000))
    assert "denylist:number" not in _rules(f"weights = [{noise}]\n") or digits in noise.replace(
        ", ", ""
    )


def test_a_zip_with_too_many_members_is_an_error(monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(P, "MAX_MEMBERS", 10)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(20):
            z.writestr(f"m{i}.txt", "x")
    allow = P.parse_allowlist("binary media/art/* -- test\n")
    fs = _judge(_job(buf.getvalue(), path="media/art/a.zip"), allow)
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable-container")]


def test_the_expansion_budget_is_shared_across_members(monkeypatch):
    """Members each under the cap, together over it."""
    import bz2
    import io
    import zipfile

    monkeypatch.setattr(P, "DECODE_MAX", 50_000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(4):
            z.writestr(f"m{i}.bz2", bz2.compress(b"a" * 20_000))
    allow = P.parse_allowlist("binary media/art/* -- test\n")
    fs = _judge(_job(buf.getvalue(), path="media/art/a.zip"), allow)
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable-container")]


def test_expansion_is_time_boxed(monkeypatch):
    import gzip

    monkeypatch.setattr(P, "DECODE_SECONDS", -1.0)
    allow = P.parse_allowlist("binary media/art/* -- test\n")
    fs = _judge(_job(gzip.compress(b"x" * 100), path="media/art/a.gz"), allow)
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable-container")]


def test_a_2mb_line_of_encoded_segments_scans_in_linear_time():
    """Four times the encoded input costs at most six times the CPU."""
    import base64
    import time

    segs = [
        base64.b64encode(f"plain words number {i:06d} %41 &amp; \\u0041".encode()).decode()
        for i in range(30000)
    ]
    line = ("x = '" + " %41 &amp; \\u0041 ".join(segs) + "'")[:2_000_000]
    assert len(line) == 2_000_000

    def cpu_seconds(size):
        scanner = _scanner()
        t0 = time.process_time()
        result = scanner.scan_text(line[:size] + "\n" + NAME + "\n", "f.py")
        elapsed = time.process_time() - t0
        assert any(f.rule == "denylist:text" for f in result.findings)
        return elapsed

    small = cpu_seconds(500_000)
    large = cpu_seconds(2_000_000)
    assert large < 6 * small, (small, large)


def test_extra_commit_headers_are_read_and_signatures_skipped():
    raw = (
        "tree " + "a" * 40 + "\nparent " + "b" * 40 + "\nauthor A <a@example.com> 1 +0000\n"
        "committer A <a@example.com> 1 +0000\nx-owner " + NAME + "\n"
        "mergetag object "
        + "c" * 40
        + "\n type commit\n tag v1\n \n released for "
        + EMAIL_USER
        + "\ngpgsig -----BEGIN PGP SIGNATURE-----\n AAAA\n -----END PGP SIGNATURE-----\n\nmsg\n"
    )
    hdr = P.extra_headers(raw)
    assert NAME in hdr and EMAIL_USER in hdr and "AAAA" not in hdr and "tree" not in hdr


def test_a_cutover_baseline_must_be_a_bound_manifest(monkeypatch, tmp_path):
    import hashlib

    f = tmp_path / "m.txt"
    url = "https://example.invalid/public.git"
    good = f"# nora-pii-baseline v2\nremote {url}\n"
    for body in (
        "",
        "abc\n",
        "# nora-pii-baseline v1\n" + "a" * 40 + "\n",  # unbound v1
        good + "not-an-id\n",
        "# nora-pii-baseline v2\n" + "a" * 40 + "\n",  # no remote
        good + f"remote {url}x\n",  # two remotes
        good.replace(url, url + "x"),  # another remote
        f"remote {url}\n" + "a" * 40 + "\n",  # no header
        f"# nora-pii-baseline v1\nremote {url}\n" + "a" * 40 + "\n",  # a v1 header
    ):
        f.write_text(body)
        monkeypatch.setenv("NORA_PII_PUSH_BASELINE", str(f))
        pin = hashlib.sha256(body.encode()).hexdigest()
        monkeypatch.setenv("NORA_PII_PUSH_BASELINE_SHA256", pin)
        with pytest.raises(P.ConfigError):
            P.baseline(P.Report(), url)
    f.write_text(good)
    monkeypatch.setenv("NORA_PII_PUSH_BASELINE_SHA256", hashlib.sha256(good.encode()).hexdigest())
    monkeypatch.setattr(P, "git", lambda *a, **k: b"")
    assert P.baseline(P.Report(), url) == set()
    for pin in ("", "0" * 64, hashlib.sha256(b"other").hexdigest()[:16]):
        monkeypatch.setenv("NORA_PII_PUSH_BASELINE_SHA256", pin)
        with pytest.raises(P.ConfigError):
            P.baseline(P.Report(), url)


def test_ls_remote_never_waits_for_a_prompt(monkeypatch):
    import subprocess

    def slow(*a, **k):
        assert k["env"]["GIT_TERMINAL_PROMPT"] == "0" and k["stdin"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired(a[0], k["timeout"])

    monkeypatch.setattr(P.subprocess, "run", slow)
    with pytest.raises(P.GitError):
        P.ls_remote("https://example.invalid/x.git")


# ---- the denylist builder's public keep-list


def test_a_kept_public_term_is_dropped_but_its_longer_siblings_stay():
    term = _invented_word().lower()
    src = [
        (term, "hostname"),
        (term.upper(), "hostname"),
        (f"{term}-dev", "author"),
        (f"{term}.tail{SY.uuid()[:6]}.ts.net", "hostname"),
        (f"/home/{term}/", "path"),
    ]
    kept, dropped = B.build(src, set(), 5, {term, f"/home/{term}/"})
    values = {v for _k, v in kept}
    assert term not in values and term.upper() not in values and f"/home/{term}/" not in values
    assert f"{term}-dev" in values and any(v.endswith(".ts.net") for v in values)
    assert dropped["public-keep"] == 3


# ---- the normalisation contract itself (each layer is also covered by a
# later, looser one, so the end-to-end tests cannot see a single layer go)


def test_normalise_line_folds_full_width_and_drops_zero_width():
    fw = "".join(chr(ord(c) + 0xFEE0) for c in "0123456789")
    assert P.normalise_line(fw) == "0123456789"
    assert P.normalise_line("a\u200bb\u200cc\ufeffd") == "abcd"


def test_match_view_folds_dashes_and_spaces_one_to_one():
    raw = "a\u2013b\u2011c\u2212d\u00a0e\u202ff"
    out = P.match_view(raw)
    assert out == "a-b-c-d e f" and len(out) == len(raw)


def test_a_denylisted_number_inside_a_long_digit_run_is_found():
    """Past the spread matcher's 24-digit window, the separator matcher still
    finds a number embedded in a longer run."""
    lead = " ".join(str(_R.randint(1000, 9999)) for _ in range(5))
    spaced = f"44 {NSN[:4]} {NSN[4:]}"  # no `+`: no shape rule helps here
    assert "denylist:number" in _rules(f"ids {lead} {spaced} {lead}\n")


def test_decoding_is_capped_per_line():
    import base64

    line = " ".join(base64.b64encode(f"plain words {i:06d}".encode()).decode() for i in range(500))
    assert len(P._b64_texts(line)) == P.LAYER_SEGMENTS


# ---- invisible and format characters inside a name

_INVISIBLE = [
    *(chr(c) for c in range(0x200B, 0x2010)),
    *(chr(c) for c in range(0x2060, 0x2065)),
    "﻿",
    "­",
    "᠎",
    "͏",
    "️",
    "︀",
    "\U000e0100",
    "\U000e0041",
    "\U000e007f",
    "⁦",
    "‮",
    "؜",
]


@pytest.mark.parametrize("ch", _INVISIBLE, ids=lambda c: f"U+{ord(c):04X}")
def test_a_name_with_an_invisible_character_inside_is_still_denied(ch):
    spelled = f"{GIVEN[:2]}{ch}{GIVEN[2:]} {FAMILY[:3]}{ch}{ch}{FAMILY[3:]}"
    assert "denylist:text" in _rules(f'owner = "{spelled}"\n')
    # and as a source-code escape (`\xad`, `\u200e`, `\U000e0041`)
    assert "denylist:text" in _rules(f"owner = {spelled!r}\n")
    cjk = CJK[0] + ch + CJK[1:]
    assert "denylist:text" in _rules(f"佢叫{cjk}呀\n")


def test_invisible_characters_inside_a_number_are_dropped():
    spread = "⁣".join(PHONE)
    assert "denylist:number" in _rules(f'p = "{spread}"\n')


# ---- keep-phrases, a private file of exact credit lines

CREDIT = f"Character design by {NAME}, 2026"


def _keep_scanner(tmp_path, monkeypatch, *phrases):
    f = tmp_path / "keep.txt"
    f.write_text("# credit lines\n" + "".join(p + "\n" for p in phrases))
    monkeypatch.setenv("NORA_PII_KEEP_PHRASES", str(f))
    monkeypatch.setenv("NORA_PII_PSEUDONYMS", str(tmp_path / "none.txt"))
    deny = tmp_path / "deny.txt"
    deny.write_text(f"text\t{NAME}\nphone\t{PHONE}\ntext\t{CJK}\n")
    monkeypatch.setenv("NORA_PII_DENYLIST", str(deny))
    d, err, _ = P.load_denylist()
    return d, err


def test_a_keep_phrase_passes_and_the_name_outside_it_fails(tmp_path, monkeypatch):
    d, err = _keep_scanner(tmp_path, monkeypatch, CREDIT)
    assert err is None
    sc = P.Scanner(d, P.Allowlist(), set())

    def rules(text):
        return [f.rule for f in sc.scan_text(text, "README.md").findings]

    assert rules(f"{CREDIT}\n") == []
    assert rules(f"_{CREDIT.replace(' by ', '  by' + chr(9))}_\n") == []  # whitespace-tolerant
    assert rules(f"{CREDIT}; contact {NAME}\n") == ["denylist:text"]  # same line, outside it
    assert rules(f"owner: {NAME}\n") == ["denylist:text"]
    # the phrase with its name changed is not the phrase
    assert rules(f"Character design by {FAMILY}, {GIVEN}, 2026\n") == ["denylist:text"]
    assert rules(f"{CREDIT.replace(NAME, NAME.upper())}\n") == ["denylist:text"]
    assert rules(f"{CREDIT.replace('2026', '2027')}\n") == ["denylist:text"]


def test_a_keep_phrase_does_not_mask_a_number_or_an_encoded_name_beside_it(tmp_path, monkeypatch):
    import base64

    d, _ = _keep_scanner(tmp_path, monkeypatch, CREDIT)
    sc = P.Scanner(d, P.Allowlist(), set())
    enc = base64.b64encode(f"owner {NAME}".encode()).decode()
    assert [f.rule for f in sc.scan_text(f"{CREDIT} {enc}\n", "a.md").findings] == ["denylist:text"]
    assert "denylist:number" in [f.rule for f in sc.scan_text(f"{CREDIT} {PHONE}\n", "a").findings]


@pytest.mark.parametrize(
    "phrase",
    [NAME, f"{NAME}.", f"{FAMILY}, {GIVEN}", "single"],
)
def test_a_keep_phrase_must_be_more_than_the_name(tmp_path, monkeypatch, phrase):
    d, err = _keep_scanner(tmp_path, monkeypatch, phrase)
    assert d is None and err.status == "ERROR" and err.rule == "keep-phrase-invalid"
    assert GIVEN not in err.note and FAMILY not in err.note


def test_no_keep_phrase_file_is_off_not_an_error(tmp_path, monkeypatch):
    d, err = _keep_scanner(tmp_path, monkeypatch, CREDIT)
    monkeypatch.setenv("NORA_PII_KEEP_PHRASES", str(tmp_path / "absent.txt"))
    d, err, _ = P.load_denylist()
    assert err is None
    assert "denylist:text" in [
        f.rule for f in P.Scanner(d, P.Allowlist(), set()).scan_text(CREDIT, "a").findings
    ]


# ---- the pseudonym registry, values the scrub map produced


def _pseudo(tmp_path, monkeypatch, *values):
    f = tmp_path / "pseudo.txt"
    f.write_text("# scrub map output\n" + "".join(v + "\n" for v in values))
    monkeypatch.setenv("NORA_PII_PSEUDONYMS", str(f))
    monkeypatch.setenv("NORA_PII_KEEP_PHRASES", str(tmp_path / "none.txt"))
    deny = tmp_path / "deny.txt"
    deny.write_text(f"text\t{NAME}\nphone\t{PHONE}\n")
    monkeypatch.setenv("NORA_PII_DENYLIST", str(deny))
    d, err, _ = P.load_denylist()
    assert err is None
    return P.Scanner(d, P.Allowlist(), set())


def test_a_registered_pseudonym_clears_its_shape_and_nothing_else(tmp_path, monkeypatch):
    fake_phone = SY.phone("44", "spaced")
    fake_mail = SY.email()
    fake_id = SY.uuid()
    sc = _pseudo(tmp_path, monkeypatch, fake_phone, fake_mail.upper(), fake_id.replace("-", ""))

    def rules(text):
        return [f.rule for f in sc.scan_text(text, "a.py").findings]

    assert rules(f'p = "{P.digits_of(fake_phone)}"  # phone\n') == []
    assert rules(f"to: {fake_mail}\n") == []
    assert rules(f"uid {fake_id}\n") == []
    assert rules(f'p = "{SY.phone("44", "spaced")}"\n') == ["phone"]  # an unregistered one
    assert rules(f"to: {SY.email()}\n") == ["email"]


def test_a_pseudonym_never_clears_a_denylist_hit(tmp_path, monkeypatch):
    """Not even the shape whose digits sit inside a real number (the
    denylist reads it as that number): the registry is asked only after."""
    spread = f"+44 {NSN[:4]} - - - {NSN[4:]}"
    inside = f"+44 {NSN[:4]} {NSN[4:8]}"  # a prefix of the real number: a shape only
    sc = _pseudo(tmp_path, monkeypatch, PHONE, spread, NAME, inside)
    for text in (
        f'p = "{PHONE}"\n',
        f'p = "{spread}"\n',
        f"who = {NAME!r}\n",
        f'p = "{inside}"\n',
    ):
        assert [f.rule for f in sc.scan_text(text, "a.py").findings][:1] in (
            ["denylist:number"],
            ["denylist:text"],
        ), text


def test_no_pseudonym_registry_is_off_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("NORA_PII_PSEUDONYMS", str(tmp_path / "absent.txt"))
    monkeypatch.setenv("NORA_PII_KEEP_PHRASES", str(tmp_path / "absent2.txt"))
    deny = tmp_path / "deny.txt"
    deny.write_text(f"text\t{NAME}\n")
    monkeypatch.setenv("NORA_PII_DENYLIST", str(deny))
    d, err, _ = P.load_denylist()
    assert err is None
    assert _rules(f'p = "{SY.phone("44", "spaced")}"\n', P.Scanner(d, P.Allowlist(), set())) == [
        "phone"
    ]


# ---- an acknowledged or declared ERROR still gets every
# denylist check the scanner can run on what it can read


def _file_ack(job, allow=None):
    """The id an author would acknowledge to get past the structural ERROR."""
    errs = [f for f in _judge(job, allow) if f.status == "ERROR"]
    assert errs, "expected a structural ERROR"
    return errs[0].fid


def _cjk_payload() -> bytes:
    return f"owner = {CJK}\n".encode()


@pytest.mark.parametrize(
    "data",
    [
        _cjk_payload() + b"\xff",  # one bad byte: undecodable
        _cjk_payload().decode().encode("utf-16"),  # UTF-16 with a BOM
        _cjk_payload().decode().encode("utf-16-le"),  # UTF-16 without one
        b"\x1f\x8b\x08\x00garbage " + _cjk_payload(),  # a corrupt gzip
        b"\x00\x01\x02BIN" + _cjk_payload() + b"\x00",  # a binary with a UTF-8 string inside
    ],
    ids=["undecodable", "utf16-bom", "utf16-le", "corrupt-gzip", "binary-utf8-string"],
)
def test_an_acknowledged_error_still_runs_the_denylist(data):
    job = _job(data, path="tests/fixtures/x.bin")
    fid = _file_ack(job)
    fs = _judge(job, acks={fid})
    assert "denylist:text" in [f.rule for f in fs], [(f.status, f.rule) for f in fs]
    # declared as binary instead of acknowledged: the same
    allow = P.parse_allowlist("binary tests/fixtures/*.bin -- test\n")
    assert "denylist:text" in [f.rule for f in _judge(job, allow, acks={fid})]
    assert "denylist:text" in [f.rule for f in _judge(job, allow)]


def test_an_unacknowledged_error_reports_the_denylist_hit_too():
    fs = _judge(_job(_cjk_payload() + b"\xff", path="tests/fixtures/x.txt"))
    assert {(f.status, f.rule) for f in fs} == {("ERROR", "undecodable"), ("FAIL", "denylist:text")}


def test_an_over_budget_container_is_still_read_for_the_denylist(monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(P, "DECODE_MAX", 16)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("notes.txt", f"owner {NAME}\n" * 4)
    allow = P.parse_allowlist("binary media/art/* -- test\n")
    job = _job(buf.getvalue(), path="media/art/a.zip")
    fid = _file_ack(job, allow)
    fs = _judge(job, allow, acks={fid})
    assert "denylist:text" in [f.rule for f in fs]


def test_a_binary_already_holding_the_value_is_not_new():
    old = b"\x00\x01BIN" + _cjk_payload() + b"\x00"
    new = old + b"\x00more\x00"
    allow = P.parse_allowlist("binary tests/fixtures/*.bin -- test\n")
    assert _judge(_job(new, old, path="tests/fixtures/x.bin"), allow) == []


# ---- no declaration form escapes the breadth rules


@pytest.mark.parametrize(
    "pattern",
    ["vendor/[!.]*", "vendor/[a-z]*", "x/?", "vendor/[!.]*/*", "[a-z]*/x.txt", "d/*[!/]"],
)
def test_a_bracket_wildcard_is_as_broad_as_a_star(pattern):
    assert P.too_broad(pattern)
    for kind in ("path-exempt", "binary", "symlink", "gitlink", "large"):
        with pytest.raises(P.AllowlistError):
            P.parse_allowlist(f"{kind} {pattern} -- why\n")
    with pytest.raises(P.AllowlistError):
        P.parse_allowlist(f"coords {pattern} sha256={'a' * 16} -- why\n")


@pytest.mark.parametrize(
    "regex",
    [
        ".*",
        ".+",
        r"\d+",
        r"\d{8,15}",
        r"44\d{10}",
        r"852\d{8}",
        r"[0-9a-f]{32}",
        r".*@gmail\.com",
        r"[^@]+@[^@]+",
        r"(?:85[23])?\d{8}",
    ],
)
def test_a_value_re_that_matches_generic_values_is_refused(regex):
    with pytest.raises(P.AllowlistError) as exc:
        P.parse_allowlist(f"value-re {regex} -- why\n")
    assert "too broad" in str(exc.value)


def test_the_repo_s_own_declarations_are_narrow():
    P.parse_allowlist((ROOT / ".githooks" / "pii-allowlist.txt").read_text())
    P.parse_allowlist(r"value-re (852)?900001\d\d -- the pseudonym range" + "\n")


def test_a_refused_declaration_never_echoes_its_pattern():
    with pytest.raises(P.AllowlistError) as exc:
        P.parse_allowlist(f"path-exempt {GIVEN}/[a-z]* -- why\n")
    assert GIVEN not in str(exc.value)
    with pytest.raises(P.AllowlistError) as exc:
        P.parse_allowlist(f"value-re ({GIVEN}|.*) -- why\n")
    assert GIVEN not in str(exc.value)


def test_a_too_broad_declaration_is_reported_without_its_pattern(capsys):
    pattern = f"people/{GIVEN}-{FAMILY}/*"
    allow = P.parse_allowlist(f"binary {pattern} -- why\n")
    paths = {f"people/{GIVEN}-{FAMILY}/f{i}.bin" for i in range(201)}
    kept, errs = P._capped(allow.entries, paths)
    assert kept == [] and [e.rule for e in errs] == ["allowlist-too-broad"]
    P.emit(P.Report(findings=errs), "staged", 0.0)
    assert GIVEN not in capsys.readouterr().err
