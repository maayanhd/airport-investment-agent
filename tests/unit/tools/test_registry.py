from typing import Any

import pytest
from pydantic import BaseModel, Field

from airport_agent.tools.registry import ToolRegistry
from airport_agent.tools.result import ErrorKind, ToolResult


class EchoArgs(BaseModel):
    text: str = Field(min_length=1, max_length=10, description="what to echo")


class EchoTool:
    name = "echo"
    description = "Repeats the text."
    input_model = EchoArgs

    def run(self, args: Any) -> ToolResult:
        return ToolResult(tool=self.name, ok=True, data={"text": args.text})


def test_definitions_have_the_shape_the_api_expects() -> None:
    (definition,) = ToolRegistry([EchoTool()]).definitions()

    assert definition["name"] == "echo"
    assert definition["description"] == "Repeats the text."
    assert definition["input_schema"]["required"] == ["text"]
    assert definition["input_schema"]["properties"]["text"]["maxLength"] == 10


def test_a_valid_call_runs_the_tool() -> None:
    result = ToolRegistry([EchoTool()]).call("echo", {"text": "hi"})

    assert (result.ok, result.data) == (True, {"text": "hi"})


def test_an_unknown_tool_is_reported_with_the_known_names() -> None:
    result = ToolRegistry([EchoTool()]).call("rm_rf", {})

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is ErrorKind.UNKNOWN_TOOL
    assert "echo" in result.error.message


@pytest.mark.parametrize("arguments", [{}, {"text": ""}, {"text": "x" * 11}, {"text": 5}])
def test_bad_arguments_are_returned_as_an_error_not_raised(arguments: dict[str, Any]) -> None:
    result = ToolRegistry([EchoTool()]).call("echo", arguments)

    assert result.error is not None
    assert result.error.kind is ErrorKind.BAD_ARGUMENTS
    assert len(result.error.message) <= 320


def test_duplicate_tool_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([EchoTool(), EchoTool()])
