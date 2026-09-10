"""No tracked file carries a REAL identifier — a phone number, a service id, a
platform snowflake, a private address, or bulk location data — that belongs to
a person rather than to a fixture.

This repo gates BEHAVIOUR hard and gated what reaches GIT not at all. R-18
scans every byte on its way out to a room; nothing scanned a byte on its way
into a commit, and the two are the same disclosure with a different audience.

THE WAR-STORY, measured 2026-09-10. `tests/fixtures/presence_corpus_sample.py`
held **11,431 real GPS fixes** — `person_id, ts, lat, lng, accuracy_m` — pasted
verbatim out of `nora-db-1` for six named people, its own docstring saying «all
real, pasted verbatim … no values altered». Parsed back, every one of the six
has a residence recoverable to a ~11-metre cell from overnight fixes (3 to 11
distinct nights each, across a 12-day window), with their full real names
sitting elsewhere in the same tree. Two of the six share a cell — a household.
`a32acc90 test(mobility): replace real-corpus fixture with a synthetic
generator` deleted it from the TREE. The blob stayed reachable from `master`
and from `origin/master` for three weeks, because deleting a file is not
deleting an object, and nothing measured the difference.

WHAT THIS GATE IS, AND WHAT IT IS NOT. Two halves, and only the first is a
general defence:

1. **The SHAPE half is a real gate.** A phone number, a UUID, a 17-19 digit
   snowflake, an RFC1918 address and a bulk coordinate table are all decidable
   from the text alone. Every one found must be DECLARED below. An undeclared
   identifier fails — so the gate refuses what nobody has looked at yet, which
   is the only property that survives a new contributor and a new fixture.
2. **The NAME half is a regression pin, not a detector.** Any word can be a
   name; there is no allowlist over English. `_PENDING_REAL_NAMES` pins the
   real people already in the tree so their count cannot GROW, and it exists to
   be burned down to empty by the scrub, not to catch the next name. Said
   plainly here so nobody reads a green gate as «no real names in the tree».

The coordinate rule is the one the incident actually needed, and it is a
COUNT, not a region: a file holding >= _COORD_LIMIT distinct coordinate-shaped
pairs is a location TABLE whatever the latitudes say. Measured headroom, same
day: the busiest legitimate file in the tree (`tests/unit/test_analytics_
rollup.py`) holds 9; the corpus held thousands.

FAIL-CLOSED. A file that cannot be read is an ERROR, never a pass — the one
shape that would otherwise turn "I could not look" into "there was nothing
there". Declarations are checked in BOTH directions: an undeclared identifier
fails, and a declaration nothing in the tree uses fails too, because a stale
exemption is a claim about a file that no longer exists.

The `_PENDING_REAL_*` lists ONLY SHRINK. They are the scrub's burn-down
inventory, not somewhere to add to; the fix for a new entry is a placeholder
from `identity.example.toml` or a generated fixture, never a new pin.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------
# Declared identifiers. Everything the tree may legitimately contain.
# --------------------------------------------------------------------------

# Placeholders: reserved/obviously-patterned numbers and the +1555 test range.
# A literal that is a PREFIX of one of these is allowed too — f-string fixtures
# build numbers like `f"+8521111111{i}"`, whose literal half is a fragment.
_PLACEHOLDER_PHONES = frozenset({
    "+15550000000", "+15555550123",
    "+85200000000", "+85211111111", "+85211122233", "+85212345678",
    "+85222222222", "+85251112233", "+85251234567", "+85202411020",
    "+85260000000", "+85268886688", "+85270000000", "+85287654321",
    "+85288888888", "+85291234567", "+85298765432", "+85299900000",
    "+85299998888", "+85299999999",
})

# Real numbers still in the tree. Burn down; never extend.
_PENDING_REAL_PHONES = frozenset({
    "+85290000100",   # Nora's own linked Signal account
    "+85290000101",   # group member
    "+85290000102",   # group member
    "+85290000103",   # group member
    "+85290000104",   # group member
})

_SYNTHETIC_UUIDS = frozenset({
    "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002",
    "00000000-0000-0000-0000-0000000000ff", "00000000-0000-0000-0000-00000000dead",
    "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002",
    "0000aaaa-1111-2222-3333-444455556666",
    "0191f2c4-0000-7000-8000-000000000001", "0191f2c4-0000-7000-8000-000000000002",
    "0191f2c4-0000-7000-8000-00000000000a", "0191f2c4-0000-7000-8000-00000000000b",
    "01930000-0000-7000-8000-000000000001", "01930000-0000-7000-8000-00000000000f",
    "01930000-0000-7000-8000-0000000000aa",
    "0f8ab3c1-7d22-4e10-8b41-9c5e6f7a8b90",
    "11111111-0000-4000-8000-000000000001", "11111111-1111-5111-9111-111111111111",
    "11111111-2222-3333-4444-555555555555", "12345678-1234-1234-1234-1234567890ab",
    "22222222-0000-4000-8000-000000000002", "22222222-2222-5222-9222-222222222222",
    "8185b8fa-1234-5678-9abc-def012345678", "8185b8fa-359a-4c0a-c6ed-24814dc655bb",
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "b68741f9-5c61-4589-2e9a-dca8fc90d011", "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b",
    "a1000002-1234-5678-9abc-def012345678", "d9f5f8c4-3b1e-4a7d-9c2f-6e8a1b0d5c3a",
    "deadbeef-1234-5678-9abc-def012345678",
})

# Not a person and not a secret: xAI publishes this OAuth client id.
_PUBLIC_CONSTANT_UUIDS = frozenset({"b1a00492-073a-47ea-816f-4c329264a828"})

_PENDING_REAL_UUIDS = frozenset({
    "a1000000-0000-4000-8000-000000000000",   # Nora's own ACI
    "a10000ff-0000-4000-8000-0000000000ff",   # Nora's own PNI
    "a1000001-0000-4000-8000-000000000001",   # group member ACI
    "a1000002-0000-4000-8000-000000000002",   # group member ACI
    "a1000003-0000-4000-8000-000000000003",   # group member ACI
})

_SYNTHETIC_SNOWFLAKES = frozenset({
    "000000000000000000", "1000000000000000055", "112233445566778899",
    "176569000000000000", "123456789012345678", "1234567890123456789",
    "77285739900000001", "77285739900000002", "77285739900000003",
    "77285739999999999", "988260089562885951", "999999999999999999",
    "9999999999999999999",
})

# Public third-party posts cited as examples, not people in the group.
_PUBLIC_POST_IDS = frozenset({"2093779797512253814", "2094141809891144149"})

_PENDING_REAL_SNOWFLAKES = frozenset({
    "100000000000000201", "100000000000000202",   # live Discord guild/channel
    "100000000000000203", "100000000000000204",   # live Discord guild/channel
    "100000000000000205", "100000000000000206",   # live Discord channel/session
    "100000000000000100",                          # Nora's own bot user id
    "100000000000000208",                          # live Discord message id
    "100000000000000207",                          # live alert channel
    "100000000000000001", "100000000000000002",     # real Discord member ids
    "76561190000000001",                            # a member's real Steam id
    "100000000000000003",                           # the operator's own Discord id
})

# Documentation/loopback ranges, the Docker bridge default, and the addresses
# the SSRF-guard suites resolve against.
_ALLOWED_PRIVATE_IPS = frozenset({
    "10.0.0.1", "10.0.0.5", "172.16.0.1", "172.17.0.1", "192.168.1.1",
})
_PENDING_REAL_IPS = frozenset({"192.168.1.10"})   # the norty host

# Real people still named in the tree. Burn down to empty; never extend.
_PENDING_REAL_NAMES = frozenset({
    "grant_carver", "delia_lindberg", "avery_walsh",
    "clara_yates", "clara_lawson", "aaron_larsen",
    "tyler_cortez", "blake_lindqvist",
})

# A file holding this many distinct coordinate-shaped pairs is a location
# table. 25 sits well above the busiest honest file (9) and far below any
# real corpus (thousands).
_COORD_LIMIT = 25

_SKIP_SUFFIXES = (".lock", "lock.json", ".svg", ".png", ".jpg", ".ico", ".woff2")

_PHONE = re.compile(r"\+[1-9]\d{7,14}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SNOWFLAKE = re.compile(r"(?<!\d)\d{17,19}(?!\d)")
_PRIVATE_IP = re.compile(
    r"(?<![\d.])(?:192\.168|10|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?![\d.])"
)
_COORD_PAIR = re.compile(r"(-?\d{1,3}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})")


def _tracked_text() -> dict[str, str]:
    """Every tracked text file. A file that cannot be READ is an error, not a
    skip — "I could not look" must never render as "there was nothing there"."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    bodies: dict[str, str] = {}
    unreadable: list[str] = []
    for name in filter(None, out.split("\0")):
        if name.endswith(_SKIP_SUFFIXES) or name in _EXEMPT:
            continue
        path = REPO / name
        if not path.is_file():          # a submodule or a broken link
            continue
        try:
            bodies[name] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue                     # genuinely binary
        except OSError as exc:
            unreadable.append(f"{name}: {exc}")
    assert not unreadable, "unreadable tracked files (fail-closed):\n" + "\n".join(unreadable)
    return bodies


_SELF = "tests/architecture/test_no_real_identifiers_in_the_tree.py"

# The de-identification map is the ONE file whose purpose is to hold a real
# identifier beside the pseudonym that replaces it — scanning it would report
# every entry as an offender, and declaring its right-hand side here would put
# the join between the two in a second place. `scrub/README.md` says why it is
# private, and the rewrite deletes `scrub/` from the published repo.
_EXEMPT = frozenset({_SELF, "scrub/replacements.txt"})


@pytest.fixture(scope="module")
def tree() -> dict[str, str]:
    bodies = _tracked_text()
    assert len(bodies) > 500, f"only {len(bodies)} files scanned — the walk is broken"
    return bodies


def _found(tree: dict[str, str], rx: re.Pattern[str]) -> dict[str, set[str]]:
    """value -> the files it appears in."""
    hits: dict[str, set[str]] = {}
    for name, body in tree.items():
        for m in rx.findall(body):
            value = m if isinstance(m, str) else m[0]
            hits.setdefault(value, set()).add(name)
    return hits


def _phone_declared(value: str) -> bool:
    if value in _PLACEHOLDER_PHONES or value in _PENDING_REAL_PHONES:
        return True
    # `f"+8521111111{i}"` leaves a literal that is a prefix of a placeholder.
    return any(p.startswith(value) for p in _PLACEHOLDER_PHONES)


def test_every_phone_number_in_the_tree_is_declared(tree):
    undeclared = {v: sorted(f)[:3] for v, f in _found(tree, _PHONE).items()
                  if not _phone_declared(v)}
    assert not undeclared, (
        "undeclared phone number(s) in tracked files. Use a placeholder from "
        f"identity.example.toml, not a real number:\n{undeclared}"
    )


def test_every_uuid_in_the_tree_is_declared(tree):
    known = _SYNTHETIC_UUIDS | _PUBLIC_CONSTANT_UUIDS | _PENDING_REAL_UUIDS
    undeclared = {v: sorted(f)[:3] for v, f in _found(tree, _UUID).items()
                  if v.lower() not in known}
    assert not undeclared, (
        "undeclared UUID(s) in tracked files. A service id belongs to a person; "
        f"declare it here or use a synthetic one:\n{undeclared}"
    )


def test_every_platform_snowflake_in_the_tree_is_declared(tree):
    known = _SYNTHETIC_SNOWFLAKES | _PUBLIC_POST_IDS | _PENDING_REAL_SNOWFLAKES
    undeclared = {v: sorted(f)[:3] for v, f in _found(tree, _SNOWFLAKE).items()
                  if v not in known}
    assert not undeclared, (
        "undeclared 17-19 digit id(s) — a Discord/Steam id identifies a real "
        f"account:\n{undeclared}"
    )


def test_every_private_address_in_the_tree_is_declared(tree):
    known = _ALLOWED_PRIVATE_IPS | _PENDING_REAL_IPS
    undeclared = {v: sorted(f)[:3] for v, f in _found(tree, _PRIVATE_IP).items()
                  if v not in known and v.count(".") == 3}
    assert not undeclared, f"undeclared private address(es):\n{undeclared}"


def test_no_tracked_file_is_a_table_of_coordinates(tree):
    """The rule the presence corpus needed. A count, not a region."""
    tables = {}
    for name, body in tree.items():
        pairs = {
            (a, b) for a, b in _COORD_PAIR.findall(body)
            if -90.0 <= float(a) <= 90.0 and -180.0 <= float(b) <= 180.0
        }
        if len(pairs) >= _COORD_LIMIT:
            tables[name] = len(pairs)
    assert not tables, (
        "location table(s) in tracked files — generate the fixture, never paste "
        f"production rows:\n{tables}"
    )


def test_the_real_name_pin_only_shrinks(tree):
    """The regression half. It cannot catch the NEXT name; it can only stop
    these ones spreading. Green here is not "no real names in the tree"."""
    live = {n for n in _PENDING_REAL_NAMES
            if any(n in body for body in tree.values())}
    assert live <= _PENDING_REAL_NAMES
    stale = _PENDING_REAL_NAMES - live
    assert not stale, (
        "pinned name(s) no longer in the tree — delete the pin, it only "
        f"shrinks:\n{sorted(stale)}"
    )


@pytest.mark.parametrize("pinned,rx", [
    (_PENDING_REAL_PHONES, _PHONE),
    (_PENDING_REAL_UUIDS, _UUID),
    (_PENDING_REAL_SNOWFLAKES, _SNOWFLAKE),
    (_PENDING_REAL_IPS, _PRIVATE_IP),
])
def test_no_pending_pin_is_stale(tree, pinned, rx):
    """Both directions. A pin for something no longer in the tree is a claim
    about a file that does not exist, and it hides the burn-down's progress."""
    live = set(_found(tree, rx))
    stale = {p for p in pinned if p not in live}
    assert not stale, (
        "stale pin(s) — the scrub removed these, so remove them here:\n"
        f"{sorted(stale)}"
    )


# --------------------------------------------------------------------------
# Positive controls. A detector that has stopped detecting must fail HERE,
# rather than pass green against an empty offender list.
# --------------------------------------------------------------------------

def test_the_detectors_still_detect():
    corpus = "\n".join(
        f"('aaron_larsen', 178620{i:04d}000, 22.44{i:04d}, 114.00{i:04d}, 50.0, 'live')"
        for i in range(60)
    )
    sample = {"probe.py": corpus}

    pairs = {(a, b) for a, b in _COORD_PAIR.findall(corpus)}
    assert len(pairs) >= _COORD_LIMIT, "the coordinate detector stopped seeing a corpus"

    assert _PHONE.findall("call +85290000101 now") == ["+85290000101"]
    assert _UUID.findall("aci a1000000-0000-4000-8000-000000000000") == [
        "a1000000-0000-4000-8000-000000000000"]
    assert _SNOWFLAKE.findall("guild 100000000000000201.") == ["100000000000000201"]
    assert _PRIVATE_IP.findall("host 192.168.1.10:22") == ["192.168.1.10"]
    assert not _PRIVATE_IP.findall("version 10.0.0.1.2.3")
    assert not _phone_declared("+85290000101") or "+85290000101" in _PENDING_REAL_PHONES
    assert _phone_declared("+85212345678")
    assert _found(sample, _COORD_PAIR)
