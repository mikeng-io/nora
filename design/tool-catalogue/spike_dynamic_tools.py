"""Gate 2b replacement: does register_dynamic_tool make a cold tool visible
to the NEXT model call and dispatchable, inside one agent loop?"""
import sys; sys.path.insert(0, ".")
from strands.tools.registry import ToolRegistry as StrandsRegistry
from strands import tool

@tool
def hot_tool(x: str) -> str:
    """A resident tool."""
    return x

@tool
def cold_tool(y: str) -> str:
    """A deferred tool the model has never seen."""
    return y

r = StrandsRegistry()
r.process_tools([hot_tool])
print("before:", sorted(s["name"] for s in r.get_all_tool_specs()))
print("  dispatch cold_tool ->", r.registry.get("cold_tool") or r.dynamic_tools.get("cold_tool"))

r.register_dynamic_tool(cold_tool)
print("after register_dynamic_tool:", sorted(s["name"] for s in r.get_all_tool_specs()))
print("  dispatch cold_tool ->", type(r.dynamic_tools.get("cold_tool")).__name__)
spec = [s for s in r.get_all_tool_specs() if s["name"] == "cold_tool"][0]
print("  spec carries description:", repr(spec.get("description"))[:60])
