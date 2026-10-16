"""`nora.domain.identity` — typed `(platform, kind, value)` identifiers and
the normalised two-value `Membership`.
"""

from __future__ import annotations

import pytest

from nora.domain.identity import (
    DISPLAY_NAME_KIND,
    Identifier,
    Membership,
    UnmatchableIdentifierError,
    id_kinds,
    metadata_keys,
)


def test_membership_has_exactly_two_values():
    """A three-value owner/admin/member set would ship a value that silently
    never matches on Signal — collapsing owner/creator/superadmin into
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
        ("telegram", "user_id", "123456789"),
        ("discord", "user_id", "100000000000000003"),
        ("whatsapp", "phone", "+85212345678"),
        ("whatsapp", "lid", "12345.1"),
        ("instagram", "fbid", "100000000000001"),
        ("instagram", "igid", "10000001"),
    ],
)
def test_identifier_accepts_every_kind_the_vocabulary_declares(platform, kind, value):
    ident = Identifier(platform=platform, kind=kind, value=value)
    assert (ident.platform, ident.kind, ident.value) == (platform, kind, value)


def test_display_name_is_never_matchable_on_any_platform():
    """Display names are shown, never matched. Constructing an
    `Identifier` with this kind is an error, not a silently-dropped key."""
    for platform in ("signal", "telegram", "discord", "whatsapp", "instagram"):
        with pytest.raises(UnmatchableIdentifierError):
            Identifier(platform=platform, kind=DISPLAY_NAME_KIND, value="Miles")


def test_a_kind_outside_the_platforms_vocabulary_is_rejected():
    with pytest.raises(UnmatchableIdentifierError):
        Identifier(platform="signal", kind="user_id", value="123")  # telegram's kind, not signal's


def test_an_unknown_platform_is_rejected():
    with pytest.raises(UnmatchableIdentifierError):
        Identifier(platform="carrier-pigeon", kind="phone", value="+1")


def test_id_kinds_matches_the_designs_table_exactly():
    assert id_kinds("signal") == {"phone", "uuid"}
    assert id_kinds("telegram") == {"user_id"}
    assert id_kinds("discord") == {"user_id"}
    assert id_kinds("whatsapp") == {"phone", "lid"}
    # TWO kinds, and they answer different
    # questions. `fbid` is `interop_messaging_user_fbid` (the messaging
    # identity, what a mention carries); `igid` is the public/social id the
    # story reads take. Neither is derivable from the other.
    assert id_kinds("instagram") == {"fbid", "igid"}


def test_a_username_is_never_a_matchable_kind_on_any_platform():
    """A handle is never matched on; the phone number or user ID is the stable identifier.

    A handle is RE-ASSIGNABLE, which is the part that makes this a safety rule
    and not a tidiness one: its owner renames at will, and the freed name can
    then be claimed by somebody else. Indexed, a rename silently unmaps a
    person and the next holder of that handle resolves AS them — and everything
    downstream trusts `person_id` completely (peer cards, `where_is`
    disclosure, reminder ownership).

    It stays ACCEPTED as metadata so identity.toml can still be read by a
    human; it is simply never matched on.
    """
    for platform in ("signal", "telegram", "discord", "instagram"):
        assert "username" not in id_kinds(platform), (
            f"{platform} must not match on a renameable handle"
        )
        assert "username" in metadata_keys(platform), (
            f"{platform} should still ACCEPT a username for readability"
        )
        with pytest.raises(UnmatchableIdentifierError):
            Identifier(platform=platform, kind="username", value="whoever")


def test_id_kinds_of_an_unknown_platform_is_empty_not_an_error():
    assert id_kinds("carrier-pigeon") == frozenset()


def test_identifier_is_frozen_and_hashable():
    a = Identifier(platform="signal", kind="phone", value="+1")
    b = Identifier(platform="signal", kind="phone", value="+1")
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_an_instagram_person_is_NEVER_matched_by_their_handle():
    """The same rule as every other platform, and it bites harder here: an
    Instagram @handle is the id a human would think to write down, it is
    renameable at will, and the freed name can be taken by somebody else.

    Asserted separately from the loop above because the Instagram failure has
    a second half the others do not: `igid` and `fbid` are BOTH numeric and
    BOTH matchable, so the temptation is to treat the handle as "the readable
    third id" rather than as metadata."""
    assert "username" in metadata_keys("instagram")
    with pytest.raises(UnmatchableIdentifierError):
        Identifier(platform="instagram", kind="username", value="example_handle")


def test_an_instagram_igid_is_not_accepted_as_an_fbid_and_vice_versa():
    """They are different numbers for the same person and choosing the wrong
    one produces NO ERROR anywhere — Nora simply never notices she was tagged.
    The vocabulary cannot prevent that by itself (both kinds are valid and
    both hold digits), so what is pinned here is that they stay two NAMED
    kinds rather than collapsing into one `id`, which is what would make the
    confusion unreportable."""
    fbid = Identifier(platform="instagram", kind="fbid", value="18247361241432477")
    igid = Identifier(platform="instagram", kind="igid", value="18247361241432477")
    assert fbid != igid
    assert {fbid, igid} == {fbid, igid}  # both hashable, and distinct
