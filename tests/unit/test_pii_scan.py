"""The identifier detector's rules, each against GENERATED values (#283).

Every value here is built at run time from a seeded generator
(`scripts/pii_judge_eval.py`'s `Synth`, plus invented names); none is copied
from a record, and this file holds no identifier-shaped literal of its own, so
the detector passes it like any other file. Each rule is checked on the form
it must catch AND on the look-alike it must not.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("pii_scan")
E = _load("pii_judge_eval")

SY = E.Synth(9001)
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


def test_project_identity_is_never_denied():
    d = P.parse_denylist(
        "text\tMiles Novak\ntext\tmilesng-io\ntext\tmiles_novak\ntext\tiam@mikeng.io\n", "t"
    )
    assert d.count == 0
    sc = P.Scanner(d, P.Allowlist(), set())
    assert (
        sc.scan_text(
            "Copyright Miles Novak <iam@mikeng.io>, github.com/mikeng-io\n", "LICENSE"
        ).findings
        == []
    )


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
    allow = P.parse_allowlist(f"value {P.digits_of(PHONE)} -- test\nvalue-re 44.* -- test\n")
    other = SY.phone("44")
    assert _rules(f'p = "{other}"\n', _scanner(allow=allow)) == []
    assert "denylist:number" in _rules(f'p = "{PHONE}"\n', _scanner(allow=allow))


# ---- coordinates: fields, rows x places (#283 criterion 1)


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
    payload = f"x = {NAME!r}\n"
    fs = _judge(_job(payload.encode("utf-16")))
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "utf16")]
    fs = _judge(_job(f"{CJK}\n".encode("utf-16")))
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "utf16")]
    fs = _judge(_job(payload.encode() + b"\xff"))
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable")]


def test_declared_binary_is_scanned_for_strings_and_media_gps():
    allow = P.parse_allowlist("binary assets/* -- test\n")
    fs = _judge(
        _job(b"\x00\x01PNG" + f"Author: {NAME} ".encode() + b"\x00", path="assets/a.png"), allow
    )
    assert "denylist:text" in [f.rule for f in fs]
    fs = _judge(_job(_jpeg_with_gps(True), path="assets/b.jpg"), allow)
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
    scissors, so neither may be skipped (review of #591 finding 8)."""
    scissors = "# " + "-" * 24 + " >8 " + "-" * 24
    for body in (f"feat: x\n\n# {NAME}\n", f"feat: x\n\n{scissors}\n{NAME}\n"):
        msg = tmp_path / "MSG"
        msg.write_text(body)
        monkeypatch.setattr(P, "rev_exists", lambda _r: None)
        monkeypatch.setattr(P, "git", lambda *a, **k: b"")
        rep = P.mode_message(_deny(), str(msg))
        assert "denylist:text" in [f.rule for f in rep.findings], body


# ---- the optional judges (a fake backend: no model here)


def _with_fake_judge(monkeypatch, scores, llm=None, error=None):
    sent = {}
    fake = types.ModuleType("pii_judge")

    class JudgeError(Exception):
        pass

    def score_lines(backend, items):
        if error:
            raise JudgeError(error)
        sent["items"] = items
        return types.SimpleNamespace(
            scores=[scores(it[2]) for it in items], threshold=0.5, seconds=0.0
        )

    def llm_rejudge(tag, items):
        sent["llm"] = items
        return [llm(it[1]) for it in items]

    fake.JudgeError, fake.score_lines, fake.llm_rejudge = JudgeError, score_lines, llm_rejudge
    monkeypatch.setitem(sys.modules, "pii_judge", fake)
    return sent


def _judged_report(text: str, old: str | None = None):
    sc = _scanner()
    rep = P.judge_blob(_job(text.encode(), old.encode() if old else None), sc, P.Allowlist(), set())
    rep.guard = sc.contains_denylisted
    return rep


def test_judge_flags_lines_and_never_sees_a_denylisted_line(monkeypatch):
    monkeypatch.setenv("NORA_PII_JUDGE", "laya")
    monkeypatch.delenv("NORA_PII_LLM", raising=False)
    sent = _with_fake_judge(monkeypatch, lambda line: 0.9 if "lives at" in line else 0.1)
    rep = _judged_report(f"x = 1\nwho = {NAME!r}\nShe lives at {SY.address()}\n")
    P.run_judges(rep)
    assert all(NAME not in it[2] for it in sent["items"])
    assert [(f.rule, f.line) for f in rep.findings if f.rule.startswith("judge")] == [
        ("judge:laya", 3)
    ]


def test_only_added_lines_go_to_the_judge(monkeypatch):
    monkeypatch.setenv("NORA_PII_JUDGE", "laya")
    monkeypatch.delenv("NORA_PII_LLM", raising=False)
    sent = _with_fake_judge(monkeypatch, lambda line: 0.0)
    old = "first = 1\nsecond = 2\n"
    rep = _judged_report(old + "third = 3\n", old)
    P.run_judges(rep)
    assert [(it[1], it[2]) for it in sent["items"]] == [(3, "third = 3")]


def test_judge_that_does_not_answer_is_an_error(monkeypatch):
    monkeypatch.setenv("NORA_PII_JUDGE", "jev")
    _with_fake_judge(monkeypatch, lambda line: 0.0, error="HTTP 529")
    rep = _judged_report("x = 1\n")
    P.run_judges(rep)
    assert P.verdict(rep) == P.ERROR


def test_llm_verdict_is_advisory_by_default(monkeypatch):
    monkeypatch.delenv("NORA_PII_JUDGE", raising=False)
    monkeypatch.delenv("NORA_PII_LLM_CLEARS", raising=False)
    monkeypatch.setenv("NORA_PII_LLM", "some-model")
    _with_fake_judge(monkeypatch, lambda line: 0.0, llm=lambda line: "fake")
    rep = _judged_report(f'a = "{SY.phone("44")}"\n')
    P.run_judges(rep)
    assert [(f.state, f.note) for f in rep.findings] == [("", "llm=fake")]
    assert P.verdict(rep) == P.FAIL


def test_llm_clears_only_what_it_calls_fake_and_never_a_denylist_hit(monkeypatch):
    monkeypatch.delenv("NORA_PII_JUDGE", raising=False)
    monkeypatch.setenv("NORA_PII_LLM_CLEARS", "1")
    monkeypatch.setenv("NORA_PII_LLM", "some-model")
    fake_phone, real_phone = SY.phone("44"), SY.phone("81")
    sent = _with_fake_judge(
        monkeypatch, lambda line: 0.0, llm=lambda line: "fake" if fake_phone in line else "real"
    )
    rep = _judged_report(f'a = "{fake_phone}"\nb = "{real_phone}"\nc = {NAME!r}\n')
    P.run_judges(rep)
    states = {f.line: (f.rule, f.state) for f in rep.findings}
    assert states == {1: ("phone", "CLEARED"), 2: ("phone", ""), 3: ("denylist:text", "")}
    assert all(NAME not in it[1] for it in sent["llm"])


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


# ---- review of #591: spellings the first version missed (finding 2)


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
    """Finding 3: part of a real number, written so the denylist's own
    matcher does not bridge it, must not become an ack-able phone shape."""
    part = "+" + P.digits_of(PHONE)[:9]
    fs = _scanner().scan_text(f"call {part} please\n", "f.py").findings
    assert [(f.rule, f.ackable) for f in fs] == [("denylist:number", False)]


def test_a_malformed_denylist_line_does_not_echo_its_value():
    with pytest.raises(P.DenylistError) as exc:
        P.parse_denylist(f"{NAME}\ttext\n", "t")
    assert NAME not in str(exc.value) and GIVEN not in str(exc.value)


# ---- cheap phone and id shapes (finding 13)


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


# ---- coordinates the first version passed (finding 6 and probes)


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


# ---- binaries (finding 5)


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

    allow = P.parse_allowlist("binary art/* -- test\n")
    ztxt = _png((b"zTXt", b"Author\0\0" + zlib.compress(NAME.encode())))
    itxt = _png((b"iTXt", b"Author\0\x01\0\0\0" + zlib.compress(NAME.encode())))
    gz = gzip.compress(f"owner: {NAME}\n".encode())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", f"<si><t>{NAME}</t></si>")
    for blob in (ztxt, itxt, gz, buf.getvalue()):
        rules = [f.rule for f in _judge(_job(blob, path="art/a.bin"), allow)]
        assert "denylist:text" in rules, blob[:8]


def test_a_decompression_bomb_is_an_error(monkeypatch):
    import gzip

    monkeypatch.setattr(P, "DECODE_MAX", 1000)
    allow = P.parse_allowlist("binary art/* -- test\n")
    fs = _judge(_job(gzip.compress(b"a" * 5000), path="art/a.gz"), allow)
    assert [(f.status, f.rule) for f in fs] == [("ERROR", "undecodable-container")]


# ---- no model sees a denylisted line, new OR already there (finding 4)


def test_a_line_that_already_held_a_denylisted_value_is_never_sent(monkeypatch):
    monkeypatch.setenv("NORA_PII_JUDGE", "laya")
    sent = _with_fake_judge(monkeypatch, lambda line: 0.0)
    old = f"a = 1\nowner = {NAME!r}  # old\nb = 2\n"
    new = f"a = 1\nowner = {NAME!r}  # new\nb = 3\n"
    rep = _judged_report(new, old)
    P.run_judges(rep)
    blob = repr(sent)
    assert "b = 3" in blob and NAME not in blob and GIVEN not in blob


def test_with_no_guard_nothing_is_sent(monkeypatch):
    monkeypatch.setenv("NORA_PII_JUDGE", "laya")
    sent = _with_fake_judge(monkeypatch, lambda line: 0.0)
    rep = P.judge_blob(_job(b"x = 1\n"), _scanner(), P.Allowlist(), set())
    P.run_judges(rep)
    assert sent.get("items") in (None, [])


# ---- the scanner itself (findings 7, 11)


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
