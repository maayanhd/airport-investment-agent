from typing import Any

from airport_agent.dataset import Dataset
from airport_agent.tools.airport_score import AirportScoreTool
from airport_agent.tools.registry import ToolRegistry, definition

TOOL_TYPES = [AirportScoreTool]


def tool_definitions() -> list[dict[str, Any]]:
    """What the tools are, without loading any data."""
    return [definition(t.name, t.description, t.input_model) for t in TOOL_TYPES]


def build_registry(data: Dataset) -> ToolRegistry:
    return ToolRegistry([tool_type(data) for tool_type in TOOL_TYPES])
