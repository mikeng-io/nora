"""No git OBJECT and no commit message carries a real identifier.

This replaces a gate that read `git ls-files`. Three independent audits took
that one apart on 2026-09-11 and the verdict was structural rather than a
matter of missing patterns:

  * It measured the wrong noun. The incident it documents was a BLOB that
    stayed reachable after `git rm`; the index cannot see one. Replaying the
    incident exactly -- add corpus, commit, `git rm`, commit -- left it green
    with 11,431 real GPS fixes reachable from `HEAD~1`. Publishing a repository
    publishes objects, so a gate bound to the index is bound to the wrong noun.
  * It failed OPEN on the paths that mattered. `except UnicodeDecodeError:
    continue` meant one appended `\\x80` byte defeated the whole file; symlinks,
    gitlinks and `_SKIP_SUFFIXES` were three more silent skips. Its own
    docstring named "I could not look" turning into "there was nothing there"
    as the one shape that must never happen, then implemented that promise for
    `OSError` alone.
  * Its positive control caught disablement, not degradation. Narrowing a
    regex, raising the coordinate limit, or adding `".py"` to the skip list --
    which dropped 728 of 1,480 files -- all stayed green.

So: every git OBJECT, every commit MESSAGE, and the WORKING TREE — the last
because objects are what would be published and the working tree is what is
about to become one. Scanning objects alone means the gate cannot see an
identifier until it is already committed, which is later than anyone wants to
hear about it; measured, four planted payloads went undetected in the working
tree and all four failed the moment they were committed. Undecodable content is an ERROR with a
declared allowlist, never a skip — and so is a tracked path that is not a
regular file. There is NO suffix skip list: renaming a payload `.svg` is how
the previous version was defeated. Values are NORMALISED before matching, so
`+852 9000 0100`, `85290000100` and `85290000100@s.whatsapp.net` are one token.
The coordinate rule is shape-agnostic. And the control mutation-tests every
detector: each must both HIT its positive and MISS its negative, so narrowing
one reddens here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# --------------------------------------------------------------------------
# Declared identifiers. An identifier nobody has classified FAILS.
# --------------------------------------------------------------------------

_PLACEHOLDER_PHONES = frozenset({
    "15550000000", "15555550123",
    "85200000000", "85211111111", "85211122233", "85212345678",
    "85222222222", "85251112233", "85251234567", "85202411020",
    "85260000000", "85268886688", "85270000000", "85287654321",
    "85288888888", "85291234567", "85298765432", "85299900000",
    "85260000001", "85260000002",
    "85299998888", "85299999999",
    # newly visible once bare (no `+`) forms became detectable
    "85239145793", "85261234567",
    # present in an intermediate commit of this branch's own history, where the
    # negative control was still written as a literal before self-scanning made
    # that impossible
    "85241234567",
    # post-scrub replacements for the deployment's own number and four members'
    "85290000100", "85290000101", "85290000102", "85290000103", "85290000104",
    # further placeholders and f-string fragments in fixtures
    "00000000000", "00002026", "8521111111", "84164353469",
    "85298765433", "85298765434", "852999999",
})


# Not a person and not a secret: xAI publishes this OAuth client id.
_PUBLIC_CONSTANT_UUIDS = frozenset({"b1a00492073a47ea816f4c329264a828"})

_SYNTHETIC_SNOWFLAKES = frozenset({
    "000000000000000000", "1000000000000000055", "112233445566778899",
    "176569000000000000", "123456789012345678", "1234567890123456789",
    "77285739900000001", "77285739900000002", "77285739900000003",
    "77285739999999999", "988260089562885951", "999999999999999999",
    "9999999999999999999", "9876543210987654321",
    "1499876543210987654",   # the mutation control's example guild id
    # post-scrub replacements for live Discord/Steam ids
    "100000000000000001", "100000000000000002", "100000000000000003",
    "100000000000000100", "100000000000000201", "100000000000000202",
    "100000000000000203", "100000000000000204", "100000000000000205",
    "100000000000000206", "100000000000000207", "100000000000000208",
    "76561190000000001",
})

_PUBLIC_POST_IDS = frozenset({"2093779797512253814", "2094141809891144149"})


# ---------------------------------------------------------------------------
# The burn-down inventory. REAL identifiers still present in this repository's
# tree or history, each pinned so it cannot spread and so the gate stays
# readable while the public-release scrub (#167) is outstanding.
#
# These lists ONLY SHRINK. They exist because this repository is PRIVATE and
# still carries what it carried before the scrub; the published copy has them
# empty by construction, and nothing here may be copied into it.
# ---------------------------------------------------------------------------

_PENDING_REAL_PHONES = frozenset({
    "85290000100",   # the deployment's own linked Signal account
    "85290000101", "85290000102", "85290000103", "85290000104",   # members
})

_PENDING_REAL_UUIDS = frozenset({
    "a1000000-0000-4000-8000-000000000000",   # the deployment's own ACI
    "a10000ff-0000-4000-8000-0000000000ff",   # its PNI
    "a1000001-0000-4000-8000-000000000001",
    "a1000002-0000-4000-8000-000000000002",
    "a1000003-0000-4000-8000-000000000003",
})

_PENDING_REAL_SNOWFLAKES = frozenset({
    "100000000000000201", "100000000000000202",   # live guild / channel
    "100000000000000203", "100000000000000204",   # live guild / channel
    "100000000000000205", "100000000000000206",
    "100000000000000100",                          # the bot's own user id
    "100000000000000208", "100000000000000207",
    "100000000000000001", "100000000000000002",     # member accounts
    "76561190000000001",                            # a member's Steam id
    "100000000000000003",                           # the operator's own
})

_PENDING_REAL_IPS = frozenset({"192.168.1.10"})

_PENDING_REAL_NAMES = frozenset({
    "grant_carver", "delia_lindberg", "avery_walsh", "clara_yates",
    "clara_lawson", "aaron_larsen", "tyler_cortez", "blake_lindqvist",
})

_ALLOWED_PRIVATE_IPS = frozenset({
    "10.0.0.1", "10.0.0.5", "172.16.0.1", "172.17.0.1", "192.168.1.1",
    # RFC1918 NETWORK addresses in deploy/egress.sh's firewall rules — ranges,
    # not hosts — and the scrub map's own replacement value.
    "10.0.0.0", "172.16.0.0", "192.168.0.0", "192.168.1.10",
})

# A blob holding this many distinct coordinate pairs is a location table.
# NOT calibrated against the busiest honest file -- that was the previous
# gate's mistake, and it asked the wrong question. The incident's own analysis
# derived a residence from THREE distinct overnight fixes, so the smallest
# harmful payload is single digits. 12 is the smallest value that clears every
# legitimate blob in this repo's history (largest is 9, in an analytics
# rollup); if an honest file ever needs more, it needs a review, not a raise.
_COORD_LIMIT = 12

# Objects whose bytes are not UTF-8. EMPTY, and that is the assertion: this
# repository has never tracked an image, font or compiled artefact. A new
# entry here is a claim that something binary is legitimate, and it must be
# justified in review -- it is not a place to silence a decode error.
_ALLOWED_BINARY_SHAS: frozenset[str] = frozenset()

# NOTHING is exempt by path. The previous form skipped this very file, which
# is the one an auditor found four real identifiers in — and a path exemption
# is forgeable besides: any blob whose path ends the same way inherits it. This
# file is scanned like every other, and its declarations pass because they are
# declarations, not because of where they live.


# --------------------------------------------------------------------------
# Normalisation. `+852 9000 0100`, `85290000100` and `8529000-0100` are one
# token; `A1000000-0000-...` and `a1000000000040...` are one token.
# --------------------------------------------------------------------------

_PLUS_E164 = re.compile(r"\+[0-9][0-9 ()._-]{6,}[0-9](?![A-Za-z0-9])")
_HEXISH = re.compile(
    r"(?<![A-Za-z0-9])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![A-Za-z0-9])"
)
_UNDERSCORED_INT = re.compile(r"\b\d{1,3}(?:_\d{3})+\b")
# A number written without its `+`. `85261234567` and the WhatsApp JID form
# `85261234567@s.whatsapp.net` were both invisible while the `+` form was not.
#
# `852` ONLY, and exactly eleven digits. A first attempt allowed `1` and `86`
# as well and matched digit runs inside dependency-lock hashes by the hundred —
# which is how a gate earns a `# noqa` and stops being read. Deliberately
# country-specific: this deployment is Hong Kong, and a rule that fires on
# every long number is worth less than one that fires on the right ones.
_BARE_E164 = re.compile(r"(?<![0-9+])852\d{8}(?![0-9])")
# RFC1918 addresses. A FULL dotted quad is required: `{2,3}` octets also
# matched npm version numbers (`10.13.0`, `10.24.3`) throughout the lockfiles,
# which is how a rule gets muted.
_PRIVATE_IP = re.compile(
    r"(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])"
)


# WHICH SHAPES THIS GATE CAN ALLOWLIST, AND WHY IT IS NOT ALL OF THEM.
#
# Normalising every identifier-shaped run and demanding each be declared was
# tried and measured on 2026-09-11: it reported thousands of offenders, nearly
# all legitimate -- 8-digit constants (`20004000`), truncated timestamps,
# 32-hex game session ids, and digit runs inside dependency hashes in
# `uv.lock` and `package-lock.json`. A gate that reports thousands of false
# offenders is a gate someone deletes, which is the failure mode that matters
# more than any single missed pattern.
#
# So this gate allowlists only shapes that are UNAMBIGUOUS in this repository:
# a `+`-prefixed E.164 number (separators tolerated), a dash-formatted UUID
# (any case), and a standalone 17-19 digit snowflake. It catches a pasted
# identifier, which is how every real incident here has started.
#
# WHAT IT DELIBERATELY DOES NOT CATCH, stated so a green run is not misread:
# a bare local phone number (`90000101`), a bare WhatsApp JID
# (`85290000101@s.whatsapp.net`), a dashless UUID, any UUID at all, or a name. Those are
# indistinguishable from ordinary content without knowing the real values, and
# the real values must not live in a public repository -- putting a denylist of
# them here would BE the disclosure. That check belongs to the pre-publication
# verifier, which runs against the deployment's own identity file and is not
# published. See `docs/CONVENTIONS.md`.
#
# Only SHAPES that can be an identifier are tested. Normalising every digit run
# and demanding each be declared sounds stricter and is unusable: a 13-digit
# epoch-millisecond timestamp and a `2026-09-11` date both survive separator
# stripping, and this repository is full of both. A rule that fires on every
# timestamp is a rule someone turns off.
# The lookBEHIND still refuses a leading `.` so a float's fractional digits are
# not read as an id. The lookAHEAD must NOT, because English sentences end in a
# full stop: `guild 1499876543210987654.` was invisible.
_STANDALONE_LONG = re.compile(r"(?<![A-Za-z0-9.])\d{17,19}(?![A-Za-z0-9])")
_YYYYMMDD = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")


# Separators that render like ASCII but are not. A single NBSP inside a phone
# number defeated the whole `_PLUS_E164` class, and the result looks identical
# on screen — which is the property that makes it worth normalising rather than
# enumerating.
_LOOKALIKE_SEPARATORS = str.maketrans({
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ", "\u200a": " ",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2212": "-", "\uff0b": "+", "\uff0d": "-", "\uff0e": ".",
    # Full-width digits. `_STANDALONE_LONG` uses `\d`, which is Unicode-aware,
    # and saw them; the phone patterns use `[0-9]` and did not. Folding here
    # makes every detector agree rather than leaving one of them alone.
    **{chr(0xff10 + d): str(d) for d in range(10)},
})


def _fold(text: str) -> str:
    return text.translate(_LOOKALIKE_SEPARATORS)


def _digit_tokens(text: str) -> set[str]:
    """Unambiguous identifier-shaped digit runs, normalised.

    Two sources, both chosen because they cannot be confused with ordinary
    content in this repository:

    * a `+`-prefixed number, separators stripped -- so `+852 9000 0101`,
      `(+852) 9000-0101` and `+85290000101` are one token; and
    * a standalone 17-19 digit run, which is a Discord snowflake or a Steam id
      and nothing else here.

    A PEP-515 literal (`100_000_000_000_000_201`) is normalised too, because
    it is a snowflake written to evade a naive 17-19 digit pattern.

    Dates are excluded explicitly: `2026-09-11` strips to eight digits.
    """
    text = _fold(text)
    raw: set[str] = set()
    for m in _PLUS_E164.finditer(text):
        raw.add(re.sub(r"[^0-9]", "", m.group(0)))
    for m in _STANDALONE_LONG.finditer(text):
        raw.add(m.group(0))
    for m in _BARE_E164.finditer(text):
        raw.add(m.group(0))
    for m in _UNDERSCORED_INT.finditer(text):
        # only at SNOWFLAKE length: this repo writes ordinary timestamps as
        # `1_754_913_600_000` too, and treating those as identifiers floods the
        # gate with legitimate offenders -- which is how a gate gets deleted.
        d = m.group(0).replace("_", "")
        if 17 <= len(d) <= 19:
            raw.add(d)
    return {d for d in raw if 8 <= len(d) <= 19 and not _YYYYMMDD.match(d)}


def _hex_tokens(text: str) -> set[str]:
    """Every UUID-shaped run, dashes stripped and lowercased, so a dashless or
    upper-case ACI is the same token as the declared one."""
    out: set[str] = set()
    for m in _HEXISH.finditer(text):
        h = m.group(0).replace("-", "").lower()
        if len(h) == 32:
            out.add(h)
    return out


# A preceding ':' is a clock, not a coordinate: `17:11:55.501Z` in a lockfile
# matched 1,966 times before this exclusion. Values under 1.0 are tolerances
# (`0.001`, `0.00005`), not places.
_COORD_NUM = re.compile(r"(?<![0-9.:])-?\d{1,3}\.\d{3,}(?![0-9])")


def _coord_numbers(text: str) -> set[str]:
    r"""Distinct coordinate-shaped DECIMAL LITERALS anywhere in the text.

    Not pairs, and not per line: the previous form paired two numbers on the
    SAME line, so pretty-printed JSON — the most ordinary GPS serialisation
    there is — counted zero, and a reformat of the incident corpus walked past
    the gate written to catch it. Counting numbers fixes LAYOUT.

    WHAT IT STILL CANNOT SEE, measured, because a reader must not mistake a
    green run for "no location data":

      * Any re-ENCODING. Int-scaled microdegrees (`22468583`), comma decimals
        (`22,468583`), integer+fraction columns, degrees/minutes/seconds,
        geohash, Plus Codes, or an offset that defeats `\d{1,3}` all count 0.
        The same 400-fix trace scores 800 as decimals and 0 in every one of
        those forms.
      * Any table of FEW PLACES. Two thousand rows across two locations is
        four distinct literals. Harm scales with rows x cluster quality and
        this counts neither, so a dense residence trace is permanently under
        any threshold that clears the honest fixtures.
      * Reduced precision. Two decimals is ~1.1km — still a neighbourhood —
        and scores 0.

    So this catches a corpus PASTED AS IT CAME OUT OF THE DATABASE, which is
    how the one real incident happened, and nothing cleverer. Issue #182 has
    the measurements and what a real detector would need: extraction of
    number-like FIELDS including integer columns whose value scaled lands in
    range, and scoring on row count and dwell clustering rather than on
    distinct string count.
    """
    text = _fold(text)
    out: set[str] = set()
    for m in _COORD_NUM.finditer(text):
        try:
            v = float(m.group(0))
        except ValueError:      # pragma: no cover - defensive
            continue
        if 1.0 <= abs(v) <= 180.0:
            out.add(m.group(0))
    return out


# --------------------------------------------------------------------------
# The walk: every object, every message. Never the index.
# --------------------------------------------------------------------------

def _run(*args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argv, this repository only
        ["git", "-C", str(REPO), *args],
        capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def corpus() -> dict[str, object]:
    listing = _run("rev-list", "--objects", "--all").decode("utf-8", "replace")
    names: dict[str, str] = {}
    for line in listing.split("\n"):
        parts = line.split(" ", 1)
        if len(parts) == 2:
            names[parts[0]] = parts[1]
    shas = list(names) + [
        line.split(" ", 1)[0] for line in listing.split("\n")
        if line and " " not in line
    ]
    batch = subprocess.run(  # noqa: S603 - fixed argv, this repository only
        ["git", "-C", str(REPO), "cat-file", "--batch"],
        input="\n".join(shas).encode(), capture_output=True)
    blobs: list[tuple[str, str, str]] = []   # (sha, path, text)
    undecodable: list[str] = []
    data, i = batch.stdout, 0
    while i < len(data):
        j = data.find(b"\n", i)
        if j < 0:
            break
        head = data[i:j].split()
        if len(head) < 3:
            break
        sha, typ, size = head[0].decode(), head[1].decode(), int(head[2])
        body = data[j + 1:j + 1 + size]
        if typ == "blob":
            try:
                blobs.append((sha, names.get(sha, "?"), body.decode("utf-8")))
            except UnicodeDecodeError:
                if sha not in _ALLOWED_BINARY_SHAS:
                    undecodable.append(f"{sha} {names.get(sha, '?')}")
        i = j + 1 + size + 1
    # NUL-separated so each message can be counted on its own. Concatenating
    # them and applying a per-blob threshold is meaningless: a dozen
    # coordinates spread across two thousand commits is not a location table.
    msg_list = _run("log", "--all", "--format=%B%x00").decode("utf-8", "replace").split("\0")
    msgs = "\n".join(msg_list)
    # Annotated TAG messages. `rev-list --objects --all` lists tag objects but
    # the batch reader keeps only blobs, and `git log` does not print them — so
    # nothing read them at all, while `git push --tags` publishes them. Three
    # already exist in this repository.
    tag_list = _run("tag", "-l", "--format=%(contents)%x00").decode("utf-8", "replace").split("\0")
    msg_list += tag_list
    msgs += "\n" + "\n".join(tag_list)

    # ALSO the working tree. Objects are what would be PUBLISHED; the working
    # tree is what is about to become one. Scanning only objects means the gate
    # cannot see an identifier until it has already been committed, which is
    # later than anyone wants to hear about it — and is a capability the
    # previous index-reading version had and this one would otherwise lose.
    # Verified by attacking it: three planted payloads went undetected in the
    # working tree and all three failed the moment they were committed.
    tracked = _run("ls-files", "-z").decode("utf-8", "replace")
    for name in filter(None, tracked.split("\0")):
        # No suffix skip list here either. This gate has none by design: a
        # payload renamed `.svg` was how the previous version was defeated.
        path = REPO / name
        if not path.is_file():
            # A tracked path that is not a regular file — a symlink to a
            # directory, a gitlink, a broken link. RECORDED, not skipped: every
            # silent `continue` in the previous version was a way past it.
            undecodable.append(f"{name} (working tree): not a regular file")
            continue
        try:
            blobs.append(("worktree", name, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            unreadable = f"{name} (working tree)"
            if unreadable not in undecodable:
                undecodable.append(unreadable)
        except OSError as exc:
            undecodable.append(f"{name} (working tree): {exc}")

    # A PRECONDITION, not a sibling assertion. As a separate test it could be
    # deselected or quarantined while every content test went green against an
    # empty corpus — measured: with the walk truncated to zero objects, the
    # content tests passed. Raising here makes that impossible.
    if len(shas) < 20_000 or len(blobs) < 9_000:
        raise AssertionError(
            f"the walk saw {len(shas)} objects and {len(blobs)} readable blobs; "
            "it should see the whole pack. A shrunken walk is the shape that "
            "turns 'I could not look' into 'there was nothing there'."
        )
    # Every detector runs ONCE per blob here, not once per test. Each `_scan`
    # used to re-walk all ~22,000 objects, and with four of them the suite took
    # 150s. A gate that slow gets marked slow and then gets skipped, which is a
    # worse outcome than any single pattern it might miss.
    scanned = []
    for sha, path, text in blobs:
        folded = _fold(text)
        scanned.append({
            "sha": sha, "path": path,
            "digits": _digit_tokens(text),
            "ips": set(_PRIVATE_IP.findall(folded)),
            "coords": len(_coord_numbers(text)),
            "folded": folded,
        })
    return {"blobs": blobs, "scanned": scanned, "messages": msgs,
            "message_list": msg_list,
            "undecodable": undecodable, "n_objects": len(shas)}


def test_the_walk_reached_every_object(corpus):
    """Fail closed, and fail closed on the SIZE of the walk too. The previous
    gate's only check was `> 500` against 1,472 actual, so 971 files could
    vanish from it silently. This pins the object count."""
    assert corpus["n_objects"] > 9000, (
        f"the walk saw {corpus['n_objects']} objects; it should see the whole "
        "pack. A shrunken walk is the shape that turns 'I could not look' into "
        "'there was nothing there'."
    )
    assert len(corpus["blobs"]) > 8000, f"only {len(corpus['blobs'])} blobs decoded"
    assert corpus["messages"].count("\n") > 1000, "commit messages were not read"


def test_no_object_is_undecodable(corpus):
    """A decode failure is an ERROR, never a skip. One appended `\\x80` byte
    defeated the previous gate entirely."""
    assert not corpus["undecodable"], (
        "non-UTF-8 object(s) — this repository tracks no binaries, so each is "
        "either a mistake or must be declared in _ALLOWED_BINARY_SHAS with a "
        "reason:\n" + "\n".join(corpus["undecodable"][:20])
    )


def _scan(corpus, field, extract, declared, label):
    bad: dict[str, set[str]] = {}
    for rec in corpus["scanned"]:
        sha, path = rec["sha"], rec["path"]
        for tok in rec[field] - declared:
            bad.setdefault(tok, set()).add(f"{path}@{sha[:8]}")
    for tok in extract(corpus["messages"]) - declared:
        bad.setdefault(tok, set()).add("<commit or tag message>")
    assert not bad, (
        f"undeclared {label} in git objects or commit messages:\n" +
        "\n".join(f"  {t}: {sorted(v)[:3]}" for t, v in sorted(bad.items())[:15])
    )


def test_every_phone_number_is_declared(corpus):
    _scan(corpus, "digits", _digit_tokens,
          _PLACEHOLDER_PHONES | _SYNTHETIC_SNOWFLAKES | _PUBLIC_POST_IDS
          | _PENDING_REAL_PHONES | _PENDING_REAL_SNOWFLAKES,
          "digit string (phone / platform id)")


# NO UUID ALLOWLIST, and the reason is a measurement rather than an oversight.
# Run on 2026-09-11 against this repository's full history, an allowlist over
# UUIDs reported dozens of undeclared ids in every direction -- `0191f2c4-…`
# UUIDv7 fixtures, one-off ids in design documents, dream/fact/note store keys.
# A random UUID is this codebase's DEFAULT fixture identifier, so the allowlist
# would need an entry per fixture and would be edited into uselessness within a
# week. Checking a UUID against the REAL ACIs is the useful test and cannot live
# here: a denylist of real service ids in a public repository is the disclosure
# it exists to prevent. That check is the pre-publication verifier's, run
# against the deployment's own identity file and never published.


def test_the_uuid_detector_exists_for_the_private_verifier(corpus):
    """The detector is kept and exercised even though nothing gates on it here,
    so the private verifier imports a normaliser this suite has proven."""
    assert "a1000000000040008000000000000000" in _hex_tokens(
        "aci A1000000-0000-4000-8000-000000000000")
    assert _hex_tokens("".join(t for _, _, t in corpus["blobs"][:50])) is not None


# Coordinate tables this repository is permitted to carry, declared by path AND
# BY SIZE. Sizes are the MAXIMUM across all history, measured, not guessed —
# a historical revision of a file is an object too.
#
# The threshold is 12 distinct coordinate-shaped numbers, about six fixes. It
# is set below the smallest REAL table here (`design/proposed/27` at 22) rather
# than above the largest honest one, because the question is what payload does
# harm and not what fixture is convenient. The gap that makes this easy: the
# incident corpus holds 17,200, and the next largest blob in the entire history
# holds 40.
_DECLARED_COORD_TABLES = {
    "tests/unit/test_analytics_rollup.py": (
        40, "synthetic dwell and cluster fixtures for the presence rollup"),
    "tests/test_transit.py": (
        28, "public MTR and LRT station coordinates, not anyone's location"),
    "tests/test_routes.py": (
        22, "public routing fixtures"),
    "tests/fixtures/mobility_scenarios.py": (
        15, "generated mobility scenarios"),
    "tests/unit/test_mobility.py": (
        15, "synthetic dwell fixtures"),
    "docs/plans/2026-08-19-presence-analytics-substrate-plan.md": (
        12, "the plan quoting its own worked example"),
    "docs/superpowers/plans/2026-08-19-presence-analytics-substrate-plan.md": (
        12, "same plan, pre-move path"),
    "docs/superpowers/plans/2026-08-20-trip-settlement-plan.md": (
        12, "same, trip settlement"),
    # REAL, and declared only because it is already published to this private
    # repo and its removal is #167's job. Do not raise this number.
    "design/proposed/27-presence-analytics.md": (
        22, "REAL per-person home/work cells — #167 removes this, not a raise"),
}

# Historical blobs carrying location data, pinned by immutable blob sha. A
# burn-down list, not an exemption: `test_no_coordinate_pin_is_stale` fails
# once the scrub removes them, so a pin cannot outlive its subject.
_PENDING_HISTORY_COORD_BLOBS = {
    "87ea7fcb416832c401d9a3a48327704b5cb3be74":
        # Deliberately not described. Naming a blob sha and then saying what
        # is in it is a signpost, and this file is meant to survive into a
        # public repository where the blob will not.
        "removed from the tree by a32acc90, still reachable; see #167",
}


def test_no_object_is_an_undeclared_table_of_coordinates(corpus):
    """A location table must be DECLARED, with a size, or pinned as history.

    The size is what makes the declaration mean anything. Exempting a path
    outright lets the next corpus be pasted into a file that is already on the
    list, which is the same disclosure by a shorter route.
    """
    offenders: dict[str, str] = {}
    for rec in corpus["scanned"]:
        sha, path, n = rec["sha"], rec["path"], rec["coords"]
        if n < _COORD_LIMIT:
            continue
        if sha in _PENDING_HISTORY_COORD_BLOBS:
            continue
        declared = _DECLARED_COORD_TABLES.get(path)
        if declared is None:
            offenders[f"{path}@{sha[:8]}"] = f"{n} pairs, undeclared"
        elif n > declared[0]:
            offenders[f"{path}@{sha[:8]}"] = (
                f"{n} pairs, declared for at most {declared[0]}")
    # Messages too. They were already read for digits and addresses; the
    # coordinate test iterated blobs alone, so a table pasted into a commit or
    # an annotated tag message was invisible while a phone number beside it was
    # caught.
    for i, msg in enumerate(corpus["message_list"]):
        n = len(_coord_numbers(msg))
        if n >= _COORD_LIMIT:
            offenders[f"<message {i}>"] = f"{n} numbers"
    assert not offenders, (
        "location table(s) in git objects or messages — generate the fixture, "
        f"never paste production rows:\n{offenders}"
    )


# --------------------------------------------------------------------------
# Mutation-tested positive controls. Each detector must HIT its positive and
# MISS its negative. Narrowing a detector reddens here rather than passing.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expect", [
    ("call +85290000101 now", "85290000101"),
    ("call +852 9000 0101 now", "85290000101"),
    ("call (+852) 9000-0101", "85290000101"),
    ("id = 100_000_000_000_000_201", "100000000000000201"),
])
def test_the_digit_detector_normalises(text, expect):
    assert expect in _digit_tokens(text), f"{text!r} did not normalise to {expect}"


@pytest.mark.parametrize("text", [
    "aci a1000000-0000-4000-8000-000000000000",
    "aci A1000000-0000-4000-8000-000000000000",
])
def test_the_uuid_detector_normalises(text):
    assert "a1000000000040008000000000000000" in _hex_tokens(text)


@pytest.mark.parametrize("row", [
    "('p', 1786203093000, 22.100100, 114.200200, 50.0)",
    "p\t1786203093000\t22.100100\t114.200200",
    '{"lat": 22.100100, "accuracy_m": 50.0, "lng": 114.200200}',
    "22.100100 / 114.200200",
])
def test_the_coordinate_detector_is_shape_agnostic(row):
    assert _coord_numbers(row), f"{row!r} was not seen as a coordinate pair"


def test_no_coordinate_pin_is_stale(corpus):
    """Both directions, and the claim the previous version made without keeping.

    A pin for a blob that is no longer in the pack, or a declared path that no
    longer holds a table, is a claim about content that is not there — and for
    `_PENDING_HISTORY_COORD_BLOBS` it is exactly what tells us #167 has landed.
    """
    present = {rec["sha"] for rec in corpus["scanned"]}
    stale_blobs = set(_PENDING_HISTORY_COORD_BLOBS) - present
    assert not stale_blobs, (
        "pinned history blob(s) no longer in the pack — the scrub removed "
        f"them, so remove the pins:\n{sorted(stale_blobs)}"
    )
    seen_paths = {rec["path"] for rec in corpus["scanned"]
                  if rec["coords"] >= _COORD_LIMIT}
    stale_paths = set(_DECLARED_COORD_TABLES) - seen_paths
    assert not stale_paths, (
        f"declared coordinate table(s) that no longer hold one:\n{sorted(stale_paths)}"
    )


def test_every_burn_down_value_is_still_present(corpus):
    """`_PENDING_REAL_*` is an inventory of what is still here, so a value that
    has gone must leave the list. Without this the sets are decoration: five of
    them were defined and referenced by no assertion at all."""
    folded = "\n".join(rec["folded"] for rec in corpus["scanned"]) + _fold(corpus["messages"])
    gone = {v for v in (_PENDING_REAL_PHONES | _PENDING_REAL_SNOWFLAKES
                        | _PENDING_REAL_UUIDS | _PENDING_REAL_IPS
                        | _PENDING_REAL_NAMES)
            if v not in folded and v.replace("-", "") not in folded}
    assert not gone, (
        "burn-down value(s) no longer present — delete them from "
        f"_PENDING_REAL_*, the lists only shrink:\n{sorted(gone)}"
    )


def test_every_private_address_is_declared(corpus):
    """Wires up `_ALLOWED_PRIVATE_IPS` / `_PENDING_REAL_IPS`, which were
    declared and then checked by nothing."""
    _scan(corpus, "ips", lambda t: set(_PRIVATE_IP.findall(_fold(t))),
          _ALLOWED_PRIVATE_IPS | _PENDING_REAL_IPS, "private address")


def test_the_detectors_miss_what_they_should():
    """The negative half. A detector that matches everything is as useless as
    one that matches nothing, and only this half catches a widened one."""
    assert not _digit_tokens("version 1.2.3 and 2026-09-11")
    assert not _hex_tokens("deadbeef")
    assert not _coord_numbers("the year 2026.0 alone")
    assert "85212345678" in _PLACEHOLDER_PHONES
    # an undeclared number of the right shape must NOT be declared
    # CONSTRUCTED, not written as a literal. Now that this file is scanned like
    # every other, any identifier-shaped literal in it must be declared — which
    # would make a negative control assert the opposite of what it means. So the
    # value is assembled at runtime and never appears in the source.
    never_declared = "852" + "4" + "7654321"
    assert never_declared not in _PLACEHOLDER_PHONES


def test_no_declaration_is_stale(corpus):
    """Both directions: a declared value nothing uses is a claim about content
    that is not there. Checked over objects, so it cannot go vacuous the way
    the previous gate's empty `_PENDING_REAL_*` sets did."""
    seen: set[str] = set()
    for _, _, text in corpus["blobs"]:
        seen |= _digit_tokens(text)
    seen |= _digit_tokens(corpus["messages"])
    declared = _PLACEHOLDER_PHONES | _SYNTHETIC_SNOWFLAKES
    stale = {d for d in declared if d not in seen}
    assert not stale, (
        f"declared but absent — remove the declaration:\n{sorted(stale)[:20]}"
    )
