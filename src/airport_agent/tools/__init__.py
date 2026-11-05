from airport_agent.dataset import Dataset
from airport_agent.tools.airport_score import AirportScoreTool
from airport_agent.tools.registry import ToolRegistry


def build_registry(data: Dataset) -> ToolRegistry:
    return ToolRegistry([AirportScoreTool(data)])
