"""Every chat platform in the id vocabulary must round-trip through the persons FILE parser —
derived from the vocabulary, not hand-listed.

WHY THIS EXISTS. A platform whose kinds are declared in `nora.domain.identity._ID_KINDS`
but whose field is missing from `nora.persons_file`'s model passes both halves' own tests,
and surfaces as **Nora crash-looping at startup in production** the first time a real
`identity.toml` carries that platform's `[[persons.<id>.<platform>]]` block:
`Extra inputs are not permitted`. Every platform goes down with her, because a
person-mapping error is fatal for every platform at once.

Nothing else feeds a real block to the parser. `identity.example.toml` documents every
platform with every block **commented out**, so a gate over that file reads zero accounts
for zero platforms and passes on an empty set.

The list is DERIVED from `_ID_KINDS` so that adding a platform to the vocabulary and
forgetting the file model is caught here rather than by an operator watching a container
restart.
"""

from __future__ import annotations

import pytest

from nora.domain.identity import _ID_KINDS, DISPLAY_NAME_KIND
from nora.persons_file import PLATFORM_FIELDS, _PersonsFile


def _chat_platforms() -> list[str]:
    """Every platform the id vocabulary declares kinds for."""
    return sorted(_ID_KINDS)


def test_the_file_model_covers_every_platform_in_the_vocabulary() -> None:
    """The cheap half: the two lists agree. A platform in the vocabulary and
    not in `PLATFORM_FIELDS` is one an operator can write and Nora cannot
    load."""
    missing = [p for p in _chat_platforms() if p not in PLATFORM_FIELDS]
    assert not missing, (
        f"declared in the id vocabulary but absent from the persons file "
        f"model: {missing} — an operator writing that block gets a fatal "
        f"startup error, taking every other platform down with it"
    )


@pytest.mark.parametrize("platform", _chat_platforms())
def test_a_real_account_block_for_every_platform_parses(platform: str) -> None:
    """The half that actually bites: build a real `identity.toml` for this
    platform, with one matchable kind, and push it through the parser.

    This is the step the example file could never take, because everything in
    it is commented out."""
    kinds = sorted(k for k in _ID_KINDS[platform] if k != DISPLAY_NAME_KIND)
    assert kinds, f"{platform} declares no matchable kind"
    kind = kinds[0]
    data = {
        "persons": {
            "probe": {
                "first_name": "Probe",
                platform: [{kind: "1234567890"}],
            }
        }
    }
    parsed = _PersonsFile.model_validate(data)
    accounts = getattr(parsed.persons["probe"], platform)
    assert accounts and accounts[0][kind] == "1234567890", (
        f"{platform} block did not survive the parser"
    )
