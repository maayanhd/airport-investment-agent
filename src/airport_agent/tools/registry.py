"""The only way the LLM reaches data: named tools with validated arguments."""

import logging
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from airport_agent.tools.result import ErrorKind, ToolResult

MAX_ERROR_CHARS = 300
logger = logging.getLogger(__name__)


def _summary(error: ValidationError) -> str:
    """Short "field: problem" list, cut at a whole item rather than mid-message."""
    items = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in error.errors()]
    shown = ""

    for item in items:
        if len(shown) + len(item) > MAX_ERROR_CHARS:
            break

        shown += ("; " if shown else "") + item

    return f"invalid arguments - {shown or items[0][:MAX_ERROR_CHARS]}"


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_model(self) -> type[BaseModel]: ...

    def run(self, args: Any) -> ToolResult: ...


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

        if len(self._tools) != len(tools):
            raise ValueError("duplicate tool names")

    def definitions(self) -> list[dict[str, Any]]:
        """The tool list in the shape the Anthropic API expects."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)

        if tool is None:
            known = ", ".join(sorted(self._tools))

            return ToolResult.failure(
                name, ErrorKind.UNKNOWN_TOOL, f"no tool {name!r}; use: {known}"
            )

        unknown = arguments.keys() - tool.input_model.model_fields.keys()

        if unknown:
            allowed = ", ".join(tool.input_model.model_fields) or "none"
            message = f"unknown argument(s): {', '.join(sorted(unknown))}; allowed: {allowed}"

            return ToolResult.failure(name, ErrorKind.BAD_ARGUMENTS, message)

        try:
            args = tool.input_model.model_validate(arguments)
        except ValidationError as error:
            return ToolResult.failure(name, ErrorKind.BAD_ARGUMENTS, _summary(error))

        try:
            return tool.run(args)
        except Exception:
            logger.exception("tool %s failed", name)

            return ToolResult.failure(
                name,
                ErrorKind.INTERNAL_ERROR,
                "the tool failed unexpectedly; no result was produced",
            )
