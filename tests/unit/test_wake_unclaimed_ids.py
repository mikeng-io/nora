"""A wake row's unclaimed ids read the same from every form a row was written in."""
import json

import pytest

from nora.infra.sql.stimuli import unclaimed_ids


@pytest.mark.parametrize(("detail", "expected"), [
    ({"unclaimed": [12, 34]}, (12, 34)),
    ({"unclaimed": "[12, 34]"}, (12, 34)),
    (json.dumps({"unclaimed": [12, 34]}), (12, 34)),
    ({"unclaimed": None}, ()),
    ({}, ()),
    (None, ()),
])
def test_unclaimed_ids_read_every_written_form(detail, expected) -> None:
    assert unclaimed_ids(detail) == expected
