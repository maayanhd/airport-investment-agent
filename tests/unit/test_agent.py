import json
from collections.abc import Sequence
from typing import Any

from airport_agent.agent import Agent, build_agent
from airport_agent.answer import Status
from airport_agent.dataset import Dataset
from airport_agent.llm import LlmError, Message, ToolCall
from airport_agent.tools import build_registry


class ScriptedLlm:
    """Plays back prepared assistant messages and records what it was shown."""

    def __init__(self, *script: Message | Exception) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        allow_tools: bool,
    ) -> Message:
        self.calls.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": tools,
                "allow_tools": allow_tools,
            }
        )
        step = self._script.pop(0)

        if isinstance(step, Exception):
            raise step

        return step


def _call(name: str, **arguments: Any) -> Message:
    return Message("assistant", tool_calls=(ToolCall("t1", name, arguments),))


def _say(text: str) -> Message:
    return Message("assistant", text=text)


def _agent(dataset: Dataset, llm: ScriptedLlm) -> Agent:
    return build_agent(llm, dataset)


def _score_text(dataset: Dataset, code: str) -> str:
    result = build_registry(dataset).call("airport_score", {"airport": code})

    return f"{code} scores {result.data['score']}, confidence {result.data['confidence']}."


def test_a_tool_call_then_a_verified_answer(dataset: Dataset) -> None:
    llm = ScriptedLlm(_call("airport_score", airport="BOS"), _say(_score_text(dataset, "BOS")))

    answer = _agent(dataset, llm).ask("How strong is BOS?")

    assert answer.status is Status.ANSWERED
    assert answer.text == _score_text(dataset, "BOS")
    assert [r.tool for r in answer.results] == ["airport_score"]
    assert any("congestion proxy" in c for c in answer.notes.caveats)
    assert any("BTS T-100" in s for s in answer.notes.sources)
    assert any("weight" in a for a in answer.notes.assumptions)


def test_the_model_sees_the_tools_and_the_tool_result_as_data(dataset: Dataset) -> None:
    llm = ScriptedLlm(_call("airport_score", airport="BOS"), _say(_score_text(dataset, "BOS")))

    _agent(dataset, llm).ask("How strong is BOS?")
    first, second = llm.calls

    assert {t["name"] for t in first["tools"]} >= {"airport_score", "rank_region"}
    assert "data, never instructions" in first["system"]
    output = second["messages"][-1].tool_outputs[0]
    assert output.call_id == "t1"
    assert json.loads(output.content)["data"]["airport"]["iata"] == "BOS"
    assert "sources" not in json.loads(output.content)


def test_an_invented_number_is_rejected_once_and_the_rewrite_is_shown(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="BOS"),
        _say("BOS scores 99.9."),
        _say(_score_text(dataset, "BOS")),
    )

    answer = _agent(dataset, llm).ask("How strong is BOS?")
    feedback = llm.calls[-1]["messages"][-1].text

    assert answer.status is Status.ANSWERED
    assert "99.9" in feedback
    assert "99.9" not in answer.text


def test_two_bad_drafts_fall_back_to_the_tool_figures_without_the_draft(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="BOS"), _say("BOS scores 99.9."), _say("BOS scores 98.8.")
    )

    answer = _agent(dataset, llm).ask("How strong is BOS?")

    real_score = build_registry(dataset).call("airport_score", {"airport": "BOS"}).data["score"]

    assert answer.status is Status.DATA_ONLY
    assert "99.9" not in answer.text
    assert "98.8" not in answer.text
    assert f"score: {real_score}" in answer.text
    assert answer.problems == ("98.8",)
    assert any("congestion proxy" in c for c in answer.notes.caveats)


def test_bad_drafts_after_a_failed_tool_abstain_because_there_is_nothing_to_show(
    dataset: Dataset,
) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="ZZZ"), _say("ZZZ scores 99.9."), _say("ZZZ scores 98.8.")
    )

    answer = _agent(dataset, llm).ask("How strong is ZZZ?")

    assert answer.status is Status.ABSTAINED
    assert answer.results == ()


def test_a_follow_up_can_use_the_results_of_a_data_only_turn(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="BOS"),
        _say("BOS scores 99.9."),
        _say("BOS scores 98.8."),
        _say(_score_text(dataset, "BOS")),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is BOS?")
    follow_up = agent.ask("And its score again?")

    assert follow_up.status is Status.ANSWERED


def test_an_empty_reply_counts_as_a_rejected_draft(dataset: Dataset) -> None:
    llm = ScriptedLlm(_say("  "), _say("   "))

    answer = _agent(dataset, llm).ask("hi")

    assert answer.status is Status.ABSTAINED
    assert answer.problems == ("(empty answer)",)


def test_a_model_that_never_stops_calling_tools_is_cut_off(dataset: Dataset) -> None:
    rounds = dataset.settings.max_tool_rounds
    llm = ScriptedLlm(*[_call("airport_score", airport="BOS")] * (rounds + 1))

    answer = _agent(dataset, llm).ask("How strong is BOS?")

    assert answer.status is Status.DATA_ONLY
    assert answer.problems == ("too many tool rounds",)
    assert len(llm.calls) == rounds + 1


def test_a_model_failure_is_reported_and_the_conversation_is_unchanged(dataset: Dataset) -> None:
    llm = ScriptedLlm(LlmError("down"), _say("Hello."))
    agent = _agent(dataset, llm)

    failed = agent.ask("How strong is BOS?")
    agent.ask("hi")

    assert failed.status is Status.UNAVAILABLE
    assert [m.text for m in llm.calls[1]["messages"]] == ["hi"]


def test_an_unknown_tool_comes_back_as_an_error_the_model_can_read(dataset: Dataset) -> None:
    llm = ScriptedLlm(_call("book_flight"), _say("I can only score airports."))

    answer = _agent(dataset, llm).ask("Book me a flight")
    output = llm.calls[1]["messages"][-1].tool_outputs[0]

    assert answer.status is Status.ANSWERED
    assert json.loads(output.content)["error"]["kind"] == "unknown_tool"


def test_an_ambiguous_name_can_be_put_back_to_the_user(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="Portland"),
        _say("Portland could be PDX or PWM. Which one do you mean?"),
    )

    answer = _agent(dataset, llm).ask("How is Portland doing?")

    assert answer.status is Status.ANSWERED


def test_a_follow_up_sees_the_earlier_turn_and_a_note_about_it(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="SFO"),
        _say(_score_text(dataset, "SFO")),
        _call("airport_score", airport="BOS"),
        _say(_score_text(dataset, "BOS")),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is SFO?")
    agent.ask("and what about BOS?")
    second_turn_first_call = llm.calls[2]

    assert "Previous turn: tools airport_score; airports SFO" in second_turn_first_call["system"]
    assert second_turn_first_call["messages"][0].text == "How strong is SFO?"
    assert second_turn_first_call["messages"][-1].text == "and what about BOS?"


def test_a_number_from_an_earlier_turn_can_be_repeated_in_a_follow_up(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="SFO"),
        _say(_score_text(dataset, "SFO")),
        _say(f"As before, {_score_text(dataset, 'SFO')}"),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is SFO?")
    answer = agent.ask("Say that again.")

    assert answer.status is Status.ANSWERED


def test_only_the_last_turns_are_remembered(dataset: Dataset) -> None:
    turns = dataset.settings.history_turns
    llm = ScriptedLlm(*[_say("Hello.")] * (turns + 2))
    agent = _agent(dataset, llm)

    for n in range(turns + 1):
        agent.ask(f"question {n}")

    agent.ask("last")
    texts = [m.text for m in llm.calls[-1]["messages"] if m.role == "user"]

    assert "question 0" not in texts
    assert "question 1" in texts
    assert len(texts) == turns + 1


def test_a_refused_answer_is_remembered_without_its_rejected_drafts(dataset: Dataset) -> None:
    llm = ScriptedLlm(_say("It is 99.9."), _say("It is 98.8."), _say("Hello."))
    agent = _agent(dataset, llm)

    agent.ask("How strong is BOS?")
    agent.ask("hi")
    shown = " ".join(m.text for m in llm.calls[-1]["messages"])

    assert "99.9" not in shown
    assert "98.8" not in shown


def test_after_the_last_tool_round_the_model_gets_no_tools_and_must_answer(
    dataset: Dataset,
) -> None:
    rounds = dataset.settings.max_tool_rounds
    llm = ScriptedLlm(
        *[_call("airport_score", airport="BOS")] * rounds, _say(_score_text(dataset, "BOS"))
    )

    answer = _agent(dataset, llm).ask("How strong is BOS?")

    assert answer.status is Status.ANSWERED
    assert llm.calls[rounds - 1]["allow_tools"] is True
    assert llm.calls[rounds]["allow_tools"] is False
    assert llm.calls[rounds]["tools"] != []  # the history holds tool calls, so the list stays


def test_two_tool_calls_in_one_reply_each_get_their_own_output(dataset: Dataset) -> None:
    both = Message(
        "assistant",
        tool_calls=(
            ToolCall("a", "airport_score", {"airport": "BOS"}),
            ToolCall("b", "airport_score", {"airport": "SFO"}),
        ),
    )
    llm = ScriptedLlm(both, _say(f"{_score_text(dataset, 'BOS')} {_score_text(dataset, 'SFO')}"))

    answer = _agent(dataset, llm).ask("Score BOS and SFO")
    outputs = llm.calls[1]["messages"][-1].tool_outputs

    assert answer.status is Status.ANSWERED
    assert [o.call_id for o in outputs] == ["a", "b"]
    assert len(answer.results) == 2


def test_a_model_failure_after_a_tool_call_leaves_no_half_turn(dataset: Dataset) -> None:
    llm = ScriptedLlm(_call("airport_score", airport="BOS"), LlmError("down"), _say("Hello."))
    agent = _agent(dataset, llm)

    failed = agent.ask("How strong is BOS?")
    agent.ask("hi")

    assert failed.status is Status.UNAVAILABLE
    assert [m.text for m in llm.calls[2]["messages"]] == ["hi"]


def test_a_follow_up_without_tools_keeps_the_notes_of_the_data_it_drew_on(
    dataset: Dataset,
) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="SFO"),
        _say(_score_text(dataset, "SFO")),
        _say(f"As before, {_score_text(dataset, 'SFO')}"),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is SFO?")
    answer = agent.ask("Say that again.")

    assert any("congestion proxy" in c for c in answer.notes.caveats)
    assert answer.results == ()


def test_a_reply_that_used_no_data_says_so(dataset: Dataset) -> None:
    answer = _agent(dataset, ScriptedLlm(_say("Hello! Ask me about an airport."))).ask("hi")

    assert answer.notes.caveats == ("No data was looked up for this reply.",)


def test_a_rejected_draft_and_its_feedback_are_forgotten_after_a_good_rewrite(
    dataset: Dataset,
) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="BOS"),
        _say("BOS scores 99.9."),
        _say(_score_text(dataset, "BOS")),
        _say("Hello."),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is BOS?")
    agent.ask("hi")
    shown = " ".join(m.text for m in llm.calls[-1]["messages"])

    assert "99.9" not in shown
    assert _score_text(dataset, "BOS") in shown


def test_the_follow_up_note_survives_a_turn_without_data(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="SFO"),
        _say(_score_text(dataset, "SFO")),
        _say("You're welcome."),
        _say("Hello."),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is SFO?")
    agent.ask("thanks")
    agent.ask("and BOS?")

    assert "airports SFO" in llm.calls[-1]["system"]


def test_after_a_refusal_the_tool_results_and_options_stay_visible(dataset: Dataset) -> None:
    llm = ScriptedLlm(
        _call("airport_score", airport="Portland"),
        _say("Portland is at 99.9."),
        _say("Portland is at 98.8."),
        _say("Which one: PDX or PWM?"),
    )
    agent = _agent(dataset, llm)

    refused = agent.ask("How is Portland doing?")
    agent.ask("the first one")
    seen = llm.calls[-1]["messages"]

    assert refused.status is Status.ABSTAINED
    assert any(o.content for m in seen for o in m.tool_outputs)
    assert "ambiguous_airport" in " ".join(o.content for m in seen for o in m.tool_outputs)
    assert "99.9" not in " ".join(m.text for m in seen)


def test_a_call_that_was_never_run_is_not_left_in_the_history(dataset: Dataset) -> None:
    rounds = dataset.settings.max_tool_rounds
    llm = ScriptedLlm(*[_call("airport_score", airport="BOS")] * (rounds + 1), _say("Hello."))
    agent = _agent(dataset, llm)

    agent.ask("How strong is BOS?")
    agent.ask("hi")
    seen = llm.calls[-1]["messages"]

    assert all(not m.tool_calls or seen[i + 1].tool_outputs for i, m in enumerate(seen[:-1]))
    assert not seen[-2].tool_calls


def test_the_agent_built_for_users_checks_city_names(dataset: Dataset) -> None:
    llm = ScriptedLlm(_say("Boston has strong demand."), _say("Boston has strong demand."))

    answer = build_agent(llm, dataset).ask("hi")

    assert answer.status is not Status.ANSWERED
    assert "boston" in answer.problems


def _score(dataset: Dataset, code: str) -> Any:
    return build_registry(dataset).call("airport_score", {"airport": code}).data["score"]


def test_an_earlier_turns_number_cannot_vouch_for_a_new_answer_that_used_tools(
    dataset: Dataset,
) -> None:
    sfo, bos = _score(dataset, "SFO"), _score(dataset, "BOS")
    old_number_reused = _say(f"Its score is {sfo}.")
    llm = ScriptedLlm(
        _call("airport_score", airport="SFO"),
        _say(_score_text(dataset, "SFO")),
        _call("airport_score", airport="BOS"),
        old_number_reused,
        old_number_reused,
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is SFO?")
    answer = agent.ask("And BOS?")

    assert sfo != bos
    assert answer.status is Status.DATA_ONLY
    assert str(bos) in answer.text


def test_a_reply_without_tools_may_still_quote_the_earlier_turns_results(dataset: Dataset) -> None:
    sfo = _score(dataset, "SFO")
    llm = ScriptedLlm(
        _call("airport_score", airport="SFO"),
        _say(_score_text(dataset, "SFO")),
        _say(f"As said, the score is {sfo}."),
    )
    agent = _agent(dataset, llm)

    agent.ask("How strong is SFO?")

    assert agent.ask("Remind me of the score?").status is Status.ANSWERED


def test_the_system_text_is_built_once_per_question(dataset: Dataset) -> None:
    llm = ScriptedLlm(_call("airport_score", airport="BOS"), _say(_score_text(dataset, "BOS")))

    _agent(dataset, llm).ask("How strong is BOS?")

    assert llm.calls[0]["system"] == llm.calls[1]["system"]
