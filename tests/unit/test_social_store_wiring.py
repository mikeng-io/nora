"""How the composition root builds the social stream's stores.

Every reader's store (the poller's and `social_read`'s) is
`readable_social_store`, which leaves out an opted-out person's items under
their current id and every retired one; the one store built without it is the
story expiry's, which deletes every person's frames' bytes. And each social
stimulus binds a room of its unit that lists its author
(`social_person_rooms`). Ids and names are placeholders.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import nora.app.composition as composition
from nora.app.composition import readable_social_store, social_person_rooms
from nora.persons import load_persons


def test_a_reader_store_leaves_out_an_opted_out_person_under_every_id(tmp_path):
    (tmp_path / "identity.toml").write_text(
        '[persons.cy]\nname = "Cy"\nsocial = false\nformer_person_ids = ["cy_old"]\n'
        '[persons.ben]\nname = "Ben"\n'
    )
    persons = load_persons(tmp_path / "identity.toml")
    store = readable_social_store(object(), persons)
    assert store._opted_out == persons.social_off_ids() == frozenset({"cy", "cy_old"})


def test_only_the_helper_and_the_story_expiry_build_the_store():
    """A store built beside the helper would read without the opt-out."""
    tree = ast.parse(inspect.getsource(composition))
    helper = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "readable_social_store"
    )
    inside = {id(n) for n in ast.walk(helper)}
    built = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "SqlSocialStreamStore" and id(n) not in inside
    ]
    # The expiry's: the engine alone, by design (it reads every person's frames).
    assert [(len(n.args), [k.arg for k in n.keywords]) for n in built] == [(1, [])]
    readers = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "readable_social_store"
    ]
    assert len(readers) == 2


def _conv(id, unit, persons, *, home=False):
    return SimpleNamespace(
        platform="signal", primary_id=id, unit=unit, home_room=home,
        persons=None if persons is None else frozenset(persons),
    )


class _Policy:
    def __init__(self, *convs):
        self.conversations = convs

    def home_rooms(self):
        return {c.unit: c for c in self.conversations if c.home_room}


def test_a_social_stimulus_binds_home_when_home_lists_them_else_a_room_that_does():
    policy = _Policy(
        _conv("side", "friends", ["ada", "bea"]),
        _conv("home", "friends", ["bea"], home=True),
        _conv("star", "friends", None),
        _conv("other", "family", ["ada"]),
    )
    assert social_person_rooms(policy) == {
        ("bea", "friends"): ("signal", "home"),
        ("ada", "friends"): ("signal", "side"),
        ("ada", "family"): ("signal", "other"),
    }
