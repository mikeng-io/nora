"""Who may delete what on a platform where only the author can, and who a touch is credited to."""

import asyncio
from types import SimpleNamespace

from nora.domain.models import NormalizedMessage, Touch
from nora.ingest.stages import touch as touch_stage
from nora.obs.metrics import Metrics


class _Audit:
    def __init__(self, target):
        self._target = target
        self.deletes = []
        self.reactions = []

    async def find_edited(self, **_):
        return self._target

    async def append_delete(self, *, message_id, message, person_id):
        self.deletes.append(person_id)
        return 7

    async def append_reaction(self, *, message_id, message, person_id):
        self.reactions.append(person_id)
        return 8

    async def hide_message(self, **_):
        return None


class _Access:
    def __init__(self, person_id):
        self._person_id = person_id

    async def resolve_ref(self, platform, sender_id):
        return SimpleNamespace(person_id=self._person_id) if self._person_id else None

    async def identity_held(self, person_id):
        return False

    async def canonical_person(self, person_id):
        return person_id


def _message(sender):
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group", sender_id=sender,
        sender_name="x", timestamp=1_000, body="", raw={},
    )


def _run(kind, *, sender, person, target_sender="+1", target_person="ada"):
    target = SimpleNamespace(id=5, sender_id=target_sender, person_id=target_person)
    audit = _Audit(target)
    result = asyncio.run(touch_stage.run(
        message=_message(sender), touch=Touch(kind=kind, target=1_000),
        audit=audit, access=_Access(person), metrics=Metrics(),
    ))
    return result, audit


def test_a_signal_delete_naming_someone_elses_message_is_refused():
    result, audit = _run("delete", sender="+2", person="bob")
    assert result.ending == "delete_refused"
    assert audit.deletes == []


def test_the_author_deleting_their_own_message_is_recorded():
    result, audit = _run("delete", sender="+1", person="ada")
    assert result.ending == "delete_recorded"
    assert audit.deletes == ["ada"]


def test_an_unattributed_reaction_is_not_credited_to_the_target_s_author():
    result, audit = _run("reaction", sender="+9", person=None)
    assert result.ending == "reaction_recorded"
    assert audit.reactions == [None]
