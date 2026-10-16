"""`Persons.instagram_igids` — the person index knows WHICH Instagram id is
which.

`Persons.index` is `(platform, value) -> person_id` and drops the id kind, so
`platform_ids("instagram", p)` returns the fbid and the igid mixed: both are
decimal strings, and the wrong one validates everywhere and names a different
account or none. Every id below is synthetic,
in `identity.example.toml`'s placeholder ranges.
"""

from __future__ import annotations

from nora.persons import load_persons


def _persons(tmp_path, body: str):
    p = tmp_path / "identity.toml"
    p.write_text(body)
    return load_persons(p)


def test_an_account_with_fbid_and_igid_returns_only_the_igid(tmp_path):
    persons = _persons(tmp_path, (
        '[persons.jordan]\nname = "Jordan"\n'
        '[[persons.jordan.instagram]]\n'
        'fbid = "100000000000001"\nigid = "10000001"\nusername = "jordan_example"\n'
    ))
    assert persons.instagram_igids("jordan") == frozenset({"10000001"})
    # Positive control that the trap is real: the kind-blind reverse lookup
    # DOES mix the two, which is the whole reason this method exists.
    assert persons.platform_ids("instagram", "jordan") == frozenset(
        {"100000000000001", "10000001"},
    )


def test_two_accounts_return_both_igids(tmp_path):
    persons = _persons(tmp_path, (
        '[persons.jordan]\nname = "Jordan"\n'
        '[[persons.jordan.instagram]]\n'
        'label = "personal"\nfbid = "100000000000001"\nigid = "10000001"\n'
        '[[persons.jordan.instagram]]\n'
        'label = "work"\nfbid = "100000000000002"\nigid = "10000002"\n'
    ))
    assert persons.instagram_igids("jordan") == frozenset({"10000001", "10000002"})


def test_an_unknown_person_returns_an_empty_set(tmp_path):
    persons = _persons(tmp_path, (
        '[persons.jordan]\nname = "Jordan"\n'
        '[[persons.jordan.instagram]]\nfbid = "100000000000001"\nigid = "10000001"\n'
        '[persons.sam]\nname = "Sam"\n'
        '[[persons.sam.instagram]]\nfbid = "100000000000003"\n'
    ))
    assert persons.instagram_igids("nobody") == frozenset()
    # A known person with only a messaging id has no igid, and must not be
    # handed the fbid in its place.
    assert persons.instagram_igids("sam") == frozenset()
