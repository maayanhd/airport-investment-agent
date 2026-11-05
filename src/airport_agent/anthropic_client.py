"""The real model behind `LlmClient`, using the Anthropic Messages API.

Only this module knows the vendor's message shapes. Every SDK failure becomes `LlmError`, so the
agent answers "unavailable" instead of crashing, and the error text never carries the key.
"""

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Self

import anthropic

from airport_agent.config import Settings
from airport_agent.llm import LlmError, Message, ToolCall, Usage

EMPTY_REPLY = "(empty reply)"  # the API rejects an assistant message with no content


class MissingApiKeyError(Exception):
    """ANTHROPIC_API_KEY is not set, so no model can be reached."""


def _assistant_blocks(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    if message.text.strip():
        blocks.append({"type": "text", "text": message.text})

    blocks += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
        for c in message.tool_calls
    ]

    return blocks or [{"type": "text", "text": EMPTY_REPLY}]


def _user_content(message: Message) -> str | list[dict[str, Any]]:
    if not message.tool_outputs:
        return message.text

    return [
        {"type": "tool_result", "tool_use_id": o.call_id, "content": o.content}
        for o in message.tool_outputs
    ]


def to_api_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    return [
        {
            "role": m.role,
            "content": _assistant_blocks(m) if m.role == "assistant" else _user_content(m),
        }
        for m in messages
    ]


def from_api_reply(reply: Any) -> Message:  # the SDK's Message, left untyped for test stand-ins
    """The assistant message in a Messages API reply; a cut-off reply is an error, not an answer."""
    if getattr(reply, "stop_reason", None) == "max_tokens":
        raise LlmError("the model's reply was cut off")

    blocks = list(getattr(reply, "content", []))
    text = "\n".join(b.text for b in blocks if b.type == "text").strip()
    calls = tuple(ToolCall(b.id, b.name, dict(b.input)) for b in blocks if b.type == "tool_use")

    return Message("assistant", text=text, tool_calls=calls)


class AnthropicClient:
    def __init__(self, send: Callable[..., Any], *, model: str, max_tokens: int) -> None:
        """`send(**request)` is `client.messages.create`; the SDK's types stay behind it."""
        self._send = send
        self._model = model
        self._max_tokens = max_tokens
        self.usage = Usage()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        environ: Mapping[str, str] = os.environ,
        *,
        model: str | None = None,
    ) -> Self:
        key = environ.get("ANTHROPIC_API_KEY")

        if not key:
            raise MissingApiKeyError("set ANTHROPIC_API_KEY to use the real model")

        sdk = anthropic.Anthropic(
            api_key=key, timeout=settings.llm_timeout_seconds, max_retries=settings.llm_retries
        )

        return cls(
            sdk.messages.create,
            model=model or settings.router_model,
            max_tokens=settings.llm_max_tokens,
        )

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": to_api_messages(messages),
        }

        if tools:  # the API rejects tool_choice without tools
            request |= {"tools": tools, "tool_choice": {"type": "auto" if allow_tools else "none"}}

        try:
            reply = self._send(**request)
        except anthropic.APIError as error:
            raise LlmError(f"the model call failed ({type(error).__name__})") from error

        self.usage.add(reply)

        return from_api_reply(reply)
