import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fake_llm import ScriptedLlm, call, say
from fastapi.testclient import TestClient

from airport_agent import api as api_module
from airport_agent.anthropic_client import MissingApiKeyError
from airport_agent.api import create_app
from airport_agent.dataset import Dataset
from airport_agent.llm import LlmError, Message
from airport_agent.tools import build_registry
from conftest import _offline_fetcher as offline_fetcher


def _client(dataset: Dataset, llm: Any) -> TestClient:
    return TestClient(create_app(dataset.settings, dataset, llm))


def _score_text(dataset: Dataset, code: str) -> str:
    result = build_registry(dataset).call("airport_score", {"airport": code})

    return f"{code} scores {result.data['score']}."


def test_health(dataset: Dataset) -> None:
    assert _client(dataset, ScriptedLlm()).get("/health").json() == {"status": "ok"}


def test_a_question_returns_the_answer_with_its_notes_and_a_session(dataset: Dataset) -> None:
    llm = ScriptedLlm(call("airport_score", airport="BOS"), say(_score_text(dataset, "BOS")))

    body = _client(dataset, llm).post("/chat", json={"message": "How strong is BOS?"}).json()

    assert body["status"] == "answered"
    assert body["text"] == _score_text(dataset, "BOS")
    assert len(body["session_id"]) == 32
    assert any("congestion proxy" in c for c in body["notes"]["caveats"])
    assert any("BTS T-100" in s for s in body["notes"]["sources"])
    assert set(body) == {"session_id", "status", "text", "notes"}  # no raw tool output


def test_a_follow_up_with_the_session_id_sees_the_earlier_turn(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        call("airport_score", airport="SFO"),
        say(_score_text(dataset, "SFO")),
        call("airport_score", airport="BOS"),
        say(_score_text(dataset, "BOS")),
    )
    client = _client(dataset, llm)

    first = client.post("/chat", json={"message": "How strong is SFO?"}).json()
    second = client.post(
        "/chat", json={"message": "and BOS?", "session_id": first["session_id"]}
    ).json()

    assert second["session_id"] == first["session_id"]
    assert llm.calls[2]["messages"][0].text == "How strong is SFO?"


def test_a_new_conversation_does_not_see_another_one(dataset: Dataset) -> None:
    llm = ScriptedLlm(say("Hello."), say("Hello again."))
    client = _client(dataset, llm)

    client.post("/chat", json={"message": "one"})
    client.post("/chat", json={"message": "two"})

    assert [m.text for m in llm.calls[1]["messages"]] == ["two"]


def test_a_message_of_only_spaces_is_rejected_before_any_model_call(dataset: Dataset) -> None:
    llm = ScriptedLlm()
    client = _client(dataset, llm)

    assert client.post("/chat", json={"message": "   \n "}).status_code == 422
    assert llm.calls == []
    assert len(client.app.state.sessions) == 0


def test_a_failed_first_question_leaves_no_session_behind(dataset: Dataset) -> None:
    app = create_app(dataset.settings, dataset, ScriptedLlm(RuntimeError("bug")))
    client = TestClient(app, raise_server_exceptions=False)

    assert client.post("/chat", json={"message": "hi"}).status_code == 500
    assert len(app.state.sessions) == 0


def test_a_failed_follow_up_keeps_its_session(dataset: Dataset) -> None:
    app = create_app(dataset.settings, dataset, ScriptedLlm(say("Hello."), RuntimeError("bug")))
    client = TestClient(app, raise_server_exceptions=False)
    session_id = client.post("/chat", json={"message": "hi"}).json()["session_id"]

    failed = client.post("/chat", json={"message": "again", "session_id": session_id})

    assert failed.status_code == 500
    assert len(app.state.sessions) == 1


def test_an_unknown_or_expired_session_is_404(dataset: Dataset) -> None:
    response = _client(dataset, ScriptedLlm()).post(
        "/chat", json={"message": "hi", "session_id": "a" * 32}
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"message": ""}, 422),
        ({"message": "hi", "session_id": "not-an-id"}, 422),
        ({"message": "hi", "extra": 1}, 422),
        ({}, 422),
    ],
)
def test_bad_requests_are_rejected_before_any_model_call(
    dataset: Dataset, body: dict[str, Any], code: int
) -> None:
    llm = ScriptedLlm()

    assert _client(dataset, llm).post("/chat", json=body).status_code == code
    assert llm.calls == []


def test_a_question_over_the_limit_is_413(dataset: Dataset) -> None:
    llm = ScriptedLlm()
    too_long = "x" * (dataset.settings.max_message_chars + 1)

    response = _client(dataset, llm).post("/chat", json={"message": too_long})

    assert response.status_code == 413
    assert llm.calls == []


def test_a_model_outage_is_an_unavailable_answer_not_a_server_error(dataset: Dataset) -> None:
    response = _client(dataset, ScriptedLlm(LlmError("down"))).post("/chat", json={"message": "hi"})

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"


def test_an_outage_on_a_first_question_keeps_no_session_and_names_none(dataset: Dataset) -> None:
    app = create_app(dataset.settings, dataset, ScriptedLlm(LlmError("down")))

    body = TestClient(app).post("/chat", json={"message": "hi"}).json()

    assert body["session_id"] is None
    assert len(app.state.sessions) == 0


def test_an_outage_on_a_follow_up_keeps_the_conversation(dataset: Dataset) -> None:
    app = create_app(dataset.settings, dataset, ScriptedLlm(say("Hello."), LlmError("down")))
    client = TestClient(app)
    first = client.post("/chat", json={"message": "hi"}).json()

    second = client.post("/chat", json={"message": "again", "session_id": first["session_id"]})

    assert second.json()["status"] == "unavailable"
    assert second.json()["session_id"] == first["session_id"]
    assert len(app.state.sessions) == 1


class SlowLlm:
    """Records whether two questions of one session ever ran at the same time."""

    def __init__(self) -> None:
        self.running = 0
        self.overlapped = False

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        self.running += 1
        self.overlapped = self.overlapped or self.running > 1
        time.sleep(0.05)
        self.running -= 1

        return say("Hello.")


def test_two_questions_in_one_session_run_one_after_the_other(dataset: Dataset) -> None:
    llm = SlowLlm()
    client = _client(dataset, llm)
    session_id = client.post("/chat", json={"message": "first"}).json()["session_id"]
    body = {"message": "again", "session_id": session_id}
    threads = [threading.Thread(target=client.post, args=("/chat",), kwargs={"json": body})
               for _ in range(3)]  # fmt: skip

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert not llm.overlapped


def test_the_server_starts_from_the_environment(
    dataset: Dataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(api_module, "HttpFetcher", offline_fetcher)

    app = api_module.app_from_environment()

    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_the_server_refuses_to_start_without_a_key_before_loading_any_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def must_not_load(*args: Any, **kwargs: Any) -> Dataset:
        raise AssertionError("the data was loaded before the key was checked")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api_module, "load_dataset", must_not_load)

    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY"):
        api_module.app_from_environment(tmp_path / "missing.env")


def test_the_server_finds_the_key_in_an_env_file(
    dataset: Dataset, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-from-file\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api_module, "HttpFetcher", offline_fetcher)

    app = api_module.app_from_environment(env_file)
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # the loader wrote it into the real environment

    assert TestClient(app).get("/health").json() == {"status": "ok"}
