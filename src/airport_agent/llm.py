"""The model as the agent sees it: messages in, one assistant message out. No vendor types."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol


class LlmError(Exception):
    """The model could not be reached or gave an unusable reply."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    content: str  # the tool result as JSON text, passed to the model as data


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()  # assistant only
    tool_outputs: tuple[ToolOutput, ...] = ()  # user only


class LlmClient(Protocol):
    def complete(
        self, *, system: str, messages: Sequence[Message], tools: list[dict[str, Any]]
    ) -> Message:
        """The next assistant message. Raises LlmError when there is none."""
        ...
