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
    arguments: dict[str, Any]  # JSON chosen by the model, validated later by the tool


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


@dataclass
class Usage:
    """What a client has used so far, to price a run."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, reply: Any) -> None:  # the vendor's reply, left untyped for test stand-ins
        usage = getattr(reply, "usage", None)

        self.requests += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0


class LlmClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],  # JSON schemas
        allow_tools: bool,
    ) -> Message:
        """The next assistant message, or LlmError. `allow_tools` False means answer in text."""
        ...
