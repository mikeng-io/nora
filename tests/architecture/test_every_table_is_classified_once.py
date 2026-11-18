from collections import Counter
from itertools import chain
from typing import get_args

from nora.infra.sql.schema import METADATA
from nora.memory.domain.registration import (
    AGGREGATE_FAMILIES,
    ITEM_SOURCES,
    NON_GRAPH_TABLES,
    NonGraphReason,
)


def test_every_table_is_classified_once() -> None:
    classifications = Counter(chain(ITEM_SOURCES, AGGREGATE_FAMILIES, NON_GRAPH_TABLES))
    assert classifications == Counter(dict.fromkeys(METADATA.tables, 1))
    assert set(NON_GRAPH_TABLES.values()) == set(get_args(NonGraphReason))
    for name in ("nora_act", "nora_act_outcome", "computer_events", "tool_call_log"):
        assert name in ITEM_SOURCES
        assert {"graph_id", "unit", "writer", "pass", "occasion", "trace", "model"} <= set(
            METADATA.tables[name].c.keys()
        )
    for name in ("nora_act_outcome", "computer_events", "tool_call_log"):
        assert "act_id" in METADATA.tables[name].c
    for name in ("bridge_action", "computer_tasks", "browser_tasks", "social_perception"):
        assert NON_GRAPH_TABLES[name] == "operational"
