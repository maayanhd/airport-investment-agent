"""The only way the LLM reaches data: named tools with validated arguments."""

from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from airport_agent.tools.result import ErrorKind, ToolResult

MAX_ERROR_CHARS = 300


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

        try:
            args = tool.input_model.model_validate(arguments)
        except ValidationError as error:
            message = f"invalid arguments: {error}"[:MAX_ERROR_CHARS]

            return ToolResult.failure(name, ErrorKind.BAD_ARGUMENTS, message)

        return tool.run(args)
