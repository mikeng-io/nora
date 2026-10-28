"""How the composition root builds the social stream's stores.

`readable_social_store` leaves out an opted-out person's items under their
current id and every retired one (that every reader is built through it is
`tests/architecture/test_social_reads_exclude_every_opted_out_id.py`). And
each social stimulus binds a room of its unit that lists its author
(`social_person_rooms`). Ids and names are placeholders.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import nora.app.composition as composition
from nora.app.composition import social_person_rooms
from nora.infra.sql.social_stream import readable_social_store
from nora.persons import load_persons


def test_a_reader_store_leaves_out_an_opted_out_person_under_every_id(tmp_path):
    (tmp_path / "identity.toml").write_text(
        '[persons.cy]\nname = "Cy"\nsocial = false\nformer_person_ids = ["cy_old"]\n'
        '[persons.ben]\nname = "Ben"\n'
    )
    persons = load_persons(tmp_path / "identity.toml")
    store = readable_social_store(object(), persons)
    assert store._opted_out == persons.social_off_ids() == frozenset({"cy", "cy_old"})


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


def test_perception_is_built_with_the_rooms():
    """Without `rooms` every social stimulus binds the home room, and a
    person listed only elsewhere in the unit is named on an agenda whose
    room cannot read them."""
    tree = ast.parse(inspect.getsource(composition))
    (call,) = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "SocialPerception"
    ]
    rooms = {k.arg: k.value for k in call.keywords}.get("rooms")
    assert isinstance(rooms, ast.Call), "SocialPerception is built without rooms"
    assert ast.unparse(rooms.func) == "LiveMapping"
    assert isinstance(rooms.args[0], ast.Lambda)
    assert ast.unparse(rooms.args[0].body) == "social_person_rooms(allowlist)"
