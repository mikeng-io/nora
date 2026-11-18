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
    assert classifications == Counter({name: 1 for name in METADATA.tables})
    assert set(NON_GRAPH_TABLES.values()) == set(get_args(NonGraphReason))
