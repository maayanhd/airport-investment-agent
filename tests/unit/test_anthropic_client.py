from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from airport_agent.anthropic_client import (
    EMPTY_REPLY,
    AnthropicClient,
    MissingApiKeyError,
    from_api_reply,
    to_api_messages,
)
from airport_agent.config import Settings
from airport_agent.llm import LlmError, Message, ToolCall, ToolOutput


class FakeApi:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)

        if self.error:
            raise self.error

        return self.reply


def _text(text: str) -> Any:
    return SimpleNamespace(type="text", text=text)


def _tool_use(id: str, name: str, **arguments: Any) -> Any:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=arguments)


def _reply(*blocks: Any, stop: str = "end_turn") -> Any:
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def _client(api: FakeApi) -> AnthropicClient:
    return AnthropicClient(api.create, model="m-1", max_tokens=300)


def test_a_request_carries_the_model_system_tools_and_messages() -> None:
    api = FakeApi(_reply(_text("Hello.")))
    tools = [{"name": "airport_score", "description": "d", "input_schema": {"type": "object"}}]

    reply = _client(api).complete(
        system="rules", messages=[Message("user", text="hi")], tools=tools, allow_tools=True
    )
    request = api.requests[0]

    assert reply == Message("assistant", text="Hello.")
    assert (request["model"], request["max_tokens"], request["system"]) == ("m-1", 300, "rules")
    assert request["tools"] == tools
    assert request["tool_choice"] == {"type": "auto"}
    assert request["messages"] == [{"role": "user", "content": "hi"}]


def test_the_last_round_forbids_tools_but_still_sends_them() -> None:
    api = FakeApi(_reply(_text("Done.")))
    tools = [{"name": "t", "description": "d", "input_schema": {}}]

    _client(api).complete(system="s", messages=[], tools=tools, allow_tools=False)

    assert api.requests[0]["tool_choice"] == {"type": "none"}
    assert api.requests[0]["tools"] == tools


def test_tool_calls_and_their_outputs_use_the_vendor_shapes() -> None:
    history = [
        Message("user", text="score BOS"),
        Message(
            "assistant",
            text="Checking.",
            tool_calls=(ToolCall("tu_1", "airport_score", {"airport": "BOS"}),),
        ),
        Message("user", tool_outputs=(ToolOutput("tu_1", '{"ok": true}'),)),
    ]

    assert to_api_messages(history) == [
        {"role": "user", "content": "score BOS"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "tu_1", "name": "airport_score",
                 "input": {"airport": "BOS"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": '{"ok": true}'}],
        },
    ]  # fmt: skip


def test_an_empty_assistant_message_is_not_sent_empty() -> None:
    [message] = to_api_messages([Message("assistant", text="  ")])

    assert message["content"] == [{"type": "text", "text": EMPTY_REPLY}]


def test_a_reply_with_text_and_tool_calls_becomes_one_message() -> None:
    reply = from_api_reply(
        _reply(
            _text("Let me check."), _text("One moment."), _tool_use("a", "rank_region", region="AK")
        )
    )

    assert reply.text == "Let me check.\nOne moment."
    assert reply.tool_calls == (ToolCall("a", "rank_region", {"region": "AK"}),)


def test_a_reply_cut_off_at_the_token_limit_is_an_error_not_an_answer() -> None:
    with pytest.raises(LlmError, match="cut off"):
        from_api_reply(_reply(_text("BOS scores 6"), stop="max_tokens"))


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
        anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")),
        anthropic.RateLimitError(
            "slow down",
            response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
            body=None,
        ),
    ],
)
def test_every_sdk_failure_becomes_an_llm_error_without_the_message(error: Exception) -> None:
    with pytest.raises(LlmError) as raised:
        _client(FakeApi(error=error)).complete(system="s", messages=[], tools=[], allow_tools=True)

    assert type(error).__name__ in str(raised.value)
    assert "slow down" not in str(raised.value)


def test_a_missing_key_stops_the_start_up_with_a_clear_message(settings: Settings) -> None:
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY"):
        AnthropicClient.from_settings(settings, environ={})


def test_the_sdk_is_built_with_the_key_and_limits_and_called_through_the_wrapper(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: dict[str, Any] = {}
    api = FakeApi(_reply(_text("Hello.")))

    class StubSdk:
        def __init__(self, **options: Any) -> None:
            built.update(options)
            self.messages = SimpleNamespace(create=api.create)

    monkeypatch.setattr("airport_agent.anthropic_client.anthropic.Anthropic", StubSdk)
    client = AnthropicClient.from_settings(settings, environ={"ANTHROPIC_API_KEY": "sk-test"})

    reply = client.complete(
        system="s", messages=[Message("user", text="hi")], tools=[], allow_tools=True
    )

    assert reply.text == "Hello."
    assert built == {
        "api_key": "sk-test",
        "timeout": settings.llm_timeout_seconds,
        "max_retries": settings.llm_retries,
    }
    assert api.requests[0]["model"] == settings.router_model
    assert api.requests[0]["max_tokens"] == settings.llm_max_tokens


def test_usage_is_counted_across_calls() -> None:
    reply = _reply(_text("hi"))
    reply.usage = SimpleNamespace(input_tokens=50, output_tokens=7)
    client = _client(FakeApi(reply))

    for _ in range(2):
        client.complete(system="s", messages=[Message("user", "q")], tools=[], allow_tools=False)

    assert (client.usage.requests, client.usage.input_tokens, client.usage.output_tokens) == (
        2,
        100,
        14,
    )


def test_a_request_without_tools_sends_no_tool_fields() -> None:
    api = FakeApi(_reply(_text("hi")))

    _client(api).complete(system="s", messages=[Message("user", "q")], tools=[], allow_tools=False)

    assert "tools" not in api.requests[0] and "tool_choice" not in api.requests[0]
