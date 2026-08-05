"""`policy.toml` — the two allowlists and the grant predicate (design/10 §4)."""

import pytest

from nora.policy import PolicyError, load_policy

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="

TOML = f"""
default = "deny"

[dm]
persons = ["miles"]

[[conversations]]
platform = "signal"
id       = "{GROUP}"
label    = "Ridgeline"
persons  = ["miles", "ada"]

[[conversations]]
platform = "telegram"
id       = "-1001234567890"
also_ids = ["-987654321"]
label    = "Work"
persons  = "*"
"""


def _write(tmp_path, body):
    p = tmp_path / "policy.toml"
    p.write_text(body)
    return p


@pytest.fixture
def policy(tmp_path):
    return load_policy(_write(tmp_path, TOML))


def _allowed(policy, conversation_id, person_id, platform="signal"):
    return policy.is_allowed(
        platform=platform, conversation_id=conversation_id, person_id=person_id
    )


# ── the grant predicate (§4.3) ───────────────────────────────────────────────


def test_a_listed_person_in_a_listed_room_is_allowed(policy):
    assert _allowed(policy, GROUP, "miles") is True
    assert _allowed(policy, GROUP, "ada") is True


def test_an_unlisted_person_in_a_listed_room_is_refused(policy):
    """A new group member is ignored until the operator adds them (§7.1)."""
    assert _allowed(policy, GROUP, "stranger") is False


def test_a_listed_person_in_an_unlisted_room_is_refused(policy):
    """Deny by default: being trusted somewhere is not being trusted here."""
    assert _allowed(policy, "some-other-group", "miles") is False


def test_the_wildcard_admits_an_unmapped_sender(policy):
    """The room is the trust boundary. D6 already settled that an unmapped
    sender is answered but never remembered; refusing them here would reverse
    that and leave `"*"` admitting only people who are already mapped, which
    is what an explicit list is for."""
    assert _allowed(policy, "-1001234567890", None, platform="telegram") is True
    assert _allowed(policy, "-1001234567890", "anyone", platform="telegram") is True


def test_an_explicit_list_still_excludes_an_unmapped_sender(policy):
    assert _allowed(policy, GROUP, None) is False


def test_the_wildcard_still_respects_the_room_allowlist(policy):
    assert _allowed(policy, "-999", "anyone", platform="telegram") is False


def test_platforms_do_not_leak_into_each_other(policy):
    """The same id string on another platform is a different conversation."""
    assert _allowed(policy, GROUP, "miles", platform="telegram") is False


def test_also_ids_match_identically(policy):
    """Telegram changes chat.id on supergroup migration and reports the old id
    once; without also_ids the allowlist silently stops matching (§4.2)."""
    assert _allowed(policy, "-987654321", "anyone", platform="telegram") is True


def test_a_label_is_never_matched_on(policy):
    assert _allowed(policy, "Ridgeline", "miles") is False


def test_dm_persons_are_parsed(policy):
    assert policy.dm_persons == frozenset({"miles"})


# ── refusing to start (§4.1) ─────────────────────────────────────────────────


def test_an_empty_allowlist_refuses_to_start(tmp_path):
    """Not silent denial: under R-5 a silently-denied Nora is
    indistinguishable from a working one in a quiet group."""
    with pytest.raises(PolicyError, match="allows no conversations"):
        load_policy(_write(tmp_path, 'default = "deny"\n'))


def test_a_missing_file_refuses_to_start(tmp_path):
    with pytest.raises(PolicyError, match="policy file not found"):
        load_policy(tmp_path / "absent.toml")


def test_allow_by_default_is_refused(tmp_path):
    body = f'default = "allow"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
    with pytest.raises(PolicyError, match="deny"):
        load_policy(_write(tmp_path, body))


def test_an_unknown_key_is_refused(tmp_path):
    """extra="forbid": a typo must be a startup failure, not a silently-open
    allowlist (`persosn = [...]` would otherwise admit nobody, quietly)."""
    body = (
        f'default = "deny"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        'persosn = ["miles"]\n'
    )
    with pytest.raises(PolicyError):
        load_policy(_write(tmp_path, body))


def test_a_bad_wildcard_string_is_refused(tmp_path):
    body = (
        f'default = "deny"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        'persons = "everyone"\n'
    )
    with pytest.raises(PolicyError, match=r"list of person_ids"):
        load_policy(_write(tmp_path, body))


def test_the_same_conversation_listed_twice_is_refused(tmp_path):
    """The second entry would be unreachable, so one of the two allowlists the
    operator thinks he wrote is doing nothing."""
    body = (
        f'default = "deny"\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        f'persons=["miles"]\n[[conversations]]\nplatform="signal"\nid="{GROUP}"\n'
        'persons=["ada"]\n'
    )
    with pytest.raises(PolicyError, match="listed twice"):
        load_policy(_write(tmp_path, body))


def test_an_also_id_colliding_with_another_room_is_refused(tmp_path):
    body = (
        'default = "deny"\n[[conversations]]\nplatform="telegram"\nid="-1"\n'
        'persons=["miles"]\n[[conversations]]\nplatform="telegram"\nid="-2"\n'
        'also_ids=["-1"]\npersons=["ada"]\n'
    )
    with pytest.raises(PolicyError, match="listed twice"):
        load_policy(_write(tmp_path, body))
