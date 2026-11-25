"""Tool discovery and argument schema for the Cognitive Graph read service."""

from nora.agent.registry import ToolRegistry

_READS = {"memory_search", "memory_read"}




def test_graph_read_arguments_use_graph_ids_and_have_no_kind_filters():
    params = ToolRegistry().tool_param_names()
    assert params["memory_read"] == {"id", "depth"}
    assert params["memory_search"] == {"query", "person", "since", "until", "depth"}
