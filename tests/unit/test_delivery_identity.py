from datetime import UTC, datetime, timedelta

from nora.memory.domain.delivery_identity import DeliveryIdentity, SubjectEdge, resolve_delivery

_PARENT_AT = datetime(2026, 1, 1, tzinfo=UTC)
_CURRENT = _PARENT_AT + timedelta(days=1)
_ROW = {"owed_to": "generated-recipient", "asserted_at": _PARENT_AT - timedelta(days=1)}
_PARENT = SubjectEdge("serves", "intention:parent-head", _PARENT_AT)
_TOPIC = SubjectEdge("about", "entity:topic-head", _CURRENT)


def test_pointer_only_subject_is_unresolved():
    pointer = SubjectEdge("about", "entity:person", _CURRENT, pointer_kind="person")
    assert resolve_delivery(_ROW, [pointer]).identity is None


def test_parent_and_entity_heads_are_an_order_free_set():
    first = resolve_delivery(_ROW, [_PARENT, _TOPIC, _PARENT])
    second = resolve_delivery(_ROW, [_TOPIC, _PARENT])
    assert first == second
    assert first.identity == DeliveryIdentity(
        ("entity:topic-head", "intention:parent-head"), "generated-recipient",
    )
    assert first.current_since == _CURRENT


def test_recipient_changes_identity():
    first = resolve_delivery(_ROW, [_PARENT])
    second = resolve_delivery({**_ROW, "owed_to": "another-recipient"}, [_PARENT])
    assert first.identity != second.identity
    assert second.identity == DeliveryIdentity(("intention:parent-head",), "another-recipient")


def test_citations_and_condition_content_do_not_change_identity():
    cited = SubjectEdge("cites", "entity:fixture-citation", _CURRENT)
    first = resolve_delivery(_ROW, [_PARENT])
    second = resolve_delivery(
        {**_ROW, "done_when": {"text": "Different condition"}}, [_PARENT, cited],
    )
    assert first == second
