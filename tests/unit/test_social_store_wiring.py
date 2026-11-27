"""How the composition root builds the social stream's stores.

`readable_social_store` leaves out an opted-out person's items under their
current id and every retired one (that every reader is built through it is
`tests/architecture/test_social_reads_exclude_every_opted_out_id.py`). And
each social stimulus binds a room of its unit that lists its author
(`social_person_rooms`). Ids and names are placeholders.
"""

from __future__ import annotations

from types import SimpleNamespace

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
        # A listed room is always absorbed; a discovered one only when ambient.
        absorbed=True,
    )


class _Policy:
    def __init__(self, *convs):
        self.conversations = convs

    def rooms(self):
        return self.conversations

    def home_rooms(self):
        return {c.unit: c for c in self.conversations if c.home_room}




