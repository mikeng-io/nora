"""`nora.domain.identity` — typed `(platform, kind, value)` identifiers and
the normalised two-value `Membership` (design/10 §3.1, §4.4, Task 2).
"""

from __future__ import annotations

import pytest

from nora.domain.identity import (
    DISPLAY_NAME_KIND,
    Identifier,
    Membership,
    UnmatchableIdentifierError,
    id_kinds,
)


def test_membership_has_exactly_two_values():
    """A three-value owner/admin/member set would ship a value that silently
    never matches on Signal (§4.4) — collapsing owner/creator/superadmin into
    ADMIN is the point, not an oversight."""
    assert {m.value for m in Membership} == {"admin", "member"}


def test_membership_values_are_the_designs_literal_strings():
    assert Membership.ADMIN.value == "admin"
    assert Membership.MEMBER.value == "member"


@pytest.mark.parametrize(
    "platform,kind,value",
    [
        ("signal", "phone", "+85212345678"),
        ("signal", "uuid", "8185b8fa-359a-4c0a-c6ed-24814dc655bb"),
        ("signal", "username", "miles.42"),
        ("telegram", "user_id", "123456789"),
        ("telegram", "username", "pinegrove"),
        ("discord", "user_id", "100000000000000003"),
        ("discord", "username", "miles"),
        ("whatsapp", "phone", "+85212345678"),
        ("whatsapp", "lid", "12345.1"),
    ],
)
def test_identifier_accepts_every_kind_the_vocabulary_declares(platform, kind, value):
    ident = Identifier(platform=platform, kind=kind, value=value)
    assert (ident.platform, ident.kind, ident.value) == (platform, kind, value)


def test_display_name_is_never_matchable_on_any_platform():
    """§1.3: display names are shown, never matched. Constructing an
    `Identifier` with this kind is an error, not a silently-dropped key."""
    for platform in ("signal", "telegram", "discord", "whatsapp"):
        with pytest.raises(UnmatchableIdentifierError):
            Identifier(platform=platform, kind=DISPLAY_NAME_KIND, value="Miles")


def test_a_kind_outside_the_platforms_vocabulary_is_rejected():
    with pytest.raises(UnmatchableIdentifierError):
        Identifier(platform="signal", kind="user_id", value="123")  # telegram's kind, not signal's


def test_an_unknown_platform_is_rejected():
    with pytest.raises(UnmatchableIdentifierError):
        Identifier(platform="carrier-pigeon", kind="phone", value="+1")


def test_id_kinds_matches_the_designs_table_exactly():
    assert id_kinds("signal") == {"phone", "uuid", "username"}
    assert id_kinds("telegram") == {"user_id", "username"}
    assert id_kinds("discord") == {"user_id", "username"}
    assert id_kinds("whatsapp") == {"phone", "lid"}


def test_id_kinds_of_an_unknown_platform_is_empty_not_an_error():
    assert id_kinds("carrier-pigeon") == frozenset()


def test_identifier_is_frozen_and_hashable():
    a = Identifier(platform="signal", kind="phone", value="+1")
    b = Identifier(platform="signal", kind="phone", value="+1")
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}
