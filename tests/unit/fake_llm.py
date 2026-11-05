"""A scripted stand-in for the model: plays back prepared messages and records what it saw."""

from collections.abc import Sequence
from typing import Any

from airport_agent.llm import Message, ToolCall


class ScriptedLlm:
    def __init__(self, *script: Message | Exception) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[dict[str, Any]]
    ) -> Message:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        step = self._script.pop(0)

        if isinstance(step, Exception):
            raise step

        return step


def call(name: str, **arguments: Any) -> Message:
    return Message("assistant", tool_calls=(ToolCall("t1", name, arguments),))


def say(text: str) -> Message:
    return Message("assistant", text=text)
