"""`load_veto_list` — and the one distinction it did not make.

**It had no test at all**, which is how a function whose docstring described
fail-closed behaviour shipped with fail-open code for its whole life. The
paragraph saying "an unreadable file raises rather than degrading: the
operator wrote a path, so reading nothing from it is a wiring gap and not a
choice" sat directly above `if path is None or not path.is_file(): return
frozenset()`.

The consequence is quiet and it is the worst kind: a bind mount that failed to
attach, a typo in `config.toml`, a symlink whose target moved — and promotion
mints an entity for every string the room decided is not a thing, with every
counter in the tier reporting a healthy pass.
"""

from __future__ import annotations

import pytest

from nora.infra.veto_list import load_veto_list


def test_no_path_configured_vetoes_nothing_extra(tmp_path):
    """The asymmetry with `policy.toml`, and it is deliberate: the rules in
    code are the floor and this only NARROWS what promotes, so a room that has
    never needed a veto list is not a room where nobody decided anything."""
    assert load_veto_list(None) == frozenset()


def test_a_configured_path_that_is_not_there_refuses_to_start(tmp_path):
    """**The case that was collapsed into the one above.** «The operator wrote
    no path» and «the operator wrote a path and it is not there» are different
    facts, and only the first is a decision.

    Raising here means Nora refuses to start, which is `policy.toml`'s posture
    and the right one: the alternative is a tier that runs, looks healthy, and
    promotes exactly what it was told not to.
    """
    missing = tmp_path / "veto.txt"

    with pytest.raises(FileNotFoundError, match="veto_file"):
        load_veto_list(missing)


def test_a_path_pointing_at_a_directory_also_refuses(tmp_path):
    """A bind mount that attached the wrong thing, rather than nothing —
    `is_file()` is false for a directory, and the failure must not depend on
    which way the mount went wrong."""
    with pytest.raises(FileNotFoundError):
        load_veto_list(tmp_path)


def test_the_file_is_read_and_comments_and_blanks_are_dropped(tmp_path):
    """The positive control for all three above: a refusal that fired on
    everything would satisfy them while making the feature unusable, and an
    empty return would satisfy the first one alone."""
    path = tmp_path / "veto.txt"
    path.write_text(
        "# things this room says are not things\n"
        "\n"
        "the weekend\n"
        "  a good time  # trailing comment\n"
        "\n",
        encoding="utf-8",
    )

    assert load_veto_list(path) == frozenset({"the weekend", "a good time"})


def test_an_empty_file_is_a_real_answer_and_not_a_missing_one(tmp_path):
    """An operator who wrote the file and emptied it has decided «nothing
    extra», and that must not be confused with the file being gone — which is
    the whole distinction this module now makes."""
    path = tmp_path / "veto.txt"
    path.write_text("# nothing yet\n", encoding="utf-8")

    assert load_veto_list(path) == frozenset()
