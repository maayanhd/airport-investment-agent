from typing import Any

from airport_agent.dataset import Dataset
from airport_agent.tools.airport_score import AirportScoreTool
from airport_agent.tools.compare_congestion import CompareCongestionTool
from airport_agent.tools.distance_profile import DistanceProfileTool
from airport_agent.tools.rank_region import RankRegionTool
from airport_agent.tools.registry import ToolRegistry, ToolType, definition
from airport_agent.tools.unmet_demand import UnmetDemandTool

TOOL_TYPES: list[ToolType] = [
    AirportScoreTool,
    RankRegionTool,
    CompareCongestionTool,
    UnmetDemandTool,
    DistanceProfileTool,
]


def tool_definitions() -> list[dict[str, Any]]:
    """What the tools are, without loading any data."""
    return [definition(t.name, t.description, t.input_model) for t in TOOL_TYPES]


def build_registry(data: Dataset) -> ToolRegistry:
    return ToolRegistry([tool_type(data) for tool_type in TOOL_TYPES])
