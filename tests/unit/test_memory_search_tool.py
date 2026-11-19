"""Tool discovery and argument schema for the Cognitive Graph read service."""

from nora.agent.catalog.groups import HONCHO_MEMORY_TOOLS
from nora.agent.memory_search import MemorySearch
from nora.agent.registry import ToolRegistry
from nora.obs.metrics import Metrics
from nora.testing.memory_index import FakeMemoryIndex

_READS = {"memory_search", "memory_read"}


def test_legacy_backend_alone_does_not_offer_graph_reads():
    legacy = MemorySearch(FakeMemoryIndex([]), Metrics())
    shadow = ToolRegistry().with_memory_search(legacy)
    assert ToolRegistry(memory_search=legacy).withheld_tools() >= _READS
    assert shadow.withheld_tools() >= _READS
    assert not _READS & ToolRegistry(graph_retrieval=object()).withheld_tools()
    assert ToolRegistry(graph_retrieval=object()).withheld_tools() >= HONCHO_MEMORY_TOOLS


def test_graph_read_arguments_use_graph_ids_and_have_no_kind_filters():
    params = ToolRegistry().tool_param_names()
    assert params["memory_read"] == {"id", "depth"}
    assert params["memory_search"] == {"query", "person", "since", "until", "depth"}
