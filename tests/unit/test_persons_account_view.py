"""`Persons.account_for` / `accounts_for` — the exact-account view beside
`index`.

`index` flattens `(platform, value) -> person_id` and loses the account
block's grouping, so a proof of account control cannot name WHICH of a
person's accounts it proves. The view keeps it: one `BoundAccount` per
`[[persons.<id>.<platform>]]` block, typed kinds retained. Every id below is
synthetic, in `identity.example.toml`'s placeholder ranges.
"""

from __future__ import annotations

import pytest

from nora.domain.identity import BoundAccount, Identifier
from nora.persons import PersonsError, load_persons


def _persons(tmp_path, body: str):
    p = tmp_path / "identity.toml"
    p.write_text(body)
    return load_persons(p)


TWO_SIGNAL = """
[persons.miles]
name = "Miles"
[[persons.miles.signal]]
label = "personal"
phone = "+85290000101"
uuid = "a1000001-0000-4000-8000-000000000001"
username = "miles.42"
[[persons.miles.signal]]
label = "business"
phone = "+85290000103"
uuid = "a1000002-0000-4000-8000-000000000002"
"""


def test_two_accounts_on_one_platform_stay_distinct(tmp_path):
    """A presented id names the account block that claimed it, never a
    re-selected "preferred" account — the whole point of the view."""
    persons = _persons(tmp_path, TWO_SIGNAL)
    personal = persons.account_for("signal", "+85290000101")
    business = persons.account_for("signal", "a1000002-0000-4000-8000-000000000002")
    assert personal is not None and business is not None
    assert personal != business
    assert (personal.label, business.label) == ("personal", "business")
    # Both still resolve to the one person — the view refines, it does not split.
    assert {a.person_id for a in (personal, business)} == {"miles"}
    accounts = persons.accounts_for("miles", "signal")
    assert len(accounts) == 2
    assert {a.label for a in accounts} == {"personal", "business"}


def test_every_kind_on_one_account_is_kept(tmp_path):
    """Signal `phone` + `uuid` are two identifiers of ONE account; the flat
    index cannot tell they were declared together."""
    persons = _persons(tmp_path, TWO_SIGNAL)
    acct = persons.account_for("signal", "a1000001-0000-4000-8000-000000000001")
    assert acct.identifiers == frozenset({
        Identifier("signal", "phone", "+85290000101"),
        Identifier("signal", "uuid", "a1000001-0000-4000-8000-000000000001"),
    })


def test_the_same_literal_under_two_kinds_keeps_both_kinds(tmp_path):
    """Kind is part of the identifier: `phone = "X"` and `uuid = "X"` in one
    block produce two identifiers, not one flattened value."""
    persons = _persons(tmp_path, (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.signal]]\n'
        'phone = "+85290000107"\nuuid = "+85290000107"\n'
    ))
    acct = persons.account_for("signal", "+85290000107")
    assert {i.kind for i in acct.identifiers} == {"phone", "uuid"}
    # And the flat index still maps it once, to the same person.
    assert persons.resolve("signal", "+85290000107").person_id == "miles"


def test_names_labels_and_handles_never_resolve_as_identity(tmp_path):
    """A `label`, a `username`, a display name and a person `name` are all
    readable metadata — none may name an account or resolve a sender."""
    persons = _persons(tmp_path, TWO_SIGNAL)
    for value in ("personal", "business", "miles.42", "Miles"):
        assert persons.account_for("signal", value) is None, value
        assert persons.resolve("signal", value) is None, value
    acct = persons.account_for("signal", "+85290000101")
    assert "miles.42" not in {i.value for i in acct.identifiers}


def test_the_view_agrees_with_resolve_for_every_indexed_id(tmp_path):
    """The two views are built in the same loop from the same block, so they
    can never disagree about who owns an id."""
    persons = _persons(tmp_path, TWO_SIGNAL)
    for (platform, value), person_id in persons.index.items():
        assert persons.account_for(platform, value).person_id == person_id


def test_a_repeated_life360_member_id_is_one_account(tmp_path):
    """`member_id` is per-ACCOUNT and legitimately repeats across one block
    per circle; the view returns that single account while `life360` keeps
    the circle split for presence."""
    persons = _persons(tmp_path, (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.life360]]\n'
        'label = "family"\nmember_id = "m-1"\ncircle_id = "fam"\n'
        '[[persons.miles.life360]]\n'
        'label = "team"\nmember_id = "m-1"\ncircle_id = "t42"\n'
    ))
    accounts = persons.accounts_for("miles", "life360")
    assert len(accounts) == 1
    (acct,) = accounts
    assert acct.identifiers == frozenset({Identifier("life360", "member_id", "m-1")})
    assert persons.account_for("life360", "m-1") == acct
    assert {m.circle_id for m in persons.life360} == {"fam", "t42"}


def test_platform_scopes_the_same_literal_to_different_accounts(tmp_path):
    """`+852...` on Signal and on WhatsApp are different accounts — provider
    scope is part of the key, never normalised away."""
    persons = _persons(tmp_path, (
        '[persons.miles]\nname = "Miles"\n'
        '[[persons.miles.signal]]\nphone = "+85212345678"\n'
        '[[persons.miles.whatsapp]]\nphone = "+85212345678"\n'
    ))
    signal = persons.account_for("signal", "+85212345678")
    whatsapp = persons.account_for("whatsapp", "+85212345678")
    assert signal != whatsapp
    assert (signal.platform, whatsapp.platform) == ("signal", "whatsapp")
    assert len(persons.accounts_for("miles")) == 2


def test_a_retired_person_id_lists_the_survivors_accounts(tmp_path):
    """`former_person_ids` aliases still resolve to the canonical person, and
    the account view honours the same alias: the historical ids are
    unmodified, the accounts belong to whoever the id resolves to."""
    persons = _persons(tmp_path, (
        '[persons.miles_novak]\nname = "Miles"\nformer_person_ids = ["miles"]\n'
        '[[persons.miles_novak.signal]]\nphone = "+85290000101"\n'
    ))
    assert persons.accounts_for("miles") == persons.accounts_for("miles_novak")
    assert len(persons.accounts_for("miles")) == 1
    assert persons.subject_id_for("miles") == "miles_novak"


def test_an_unknown_id_or_person_is_empty_not_an_error(tmp_path):
    persons = _persons(tmp_path, TWO_SIGNAL)
    assert persons.account_for("signal", "+85200000000") is None
    assert persons.account_for("telegram", "+85290000101") is None
    assert persons.accounts_for("nobody") == ()


def test_a_duplicate_id_across_persons_is_still_refused(tmp_path):
    """The fail-closed collision rule is unchanged: the same literal under two
    people cannot produce an ambiguous account either."""
    with pytest.raises(PersonsError, match="duplicate"):
        _persons(tmp_path, (
            '[persons.miles]\nname = "Miles"\n'
            '[[persons.miles.signal]]\nphone = "+85290000101"\n'
            '[persons.clone]\nname = "Clone"\n'
            '[[persons.clone.signal]]\nphone = "+85290000101"\n'
        ))


def test_an_account_with_no_matchable_identifier_cannot_be_built():
    """The file validator refuses id-less blocks at parse; the domain type
    refuses the same state so a managed binding cannot manufacture it."""
    with pytest.raises(ValueError, match="no matchable identifier"):
        BoundAccount(person_id="miles", platform="signal", identifiers=frozenset())
