import pytest

from airport_agent.config import Settings
from airport_agent.scope import out_of_scope_topic


@pytest.mark.parametrize(
    "question",
    [
        "What is the weather in Boston tomorrow?",
        "Book me a flight to Denver",
        "Should I buy airline stocks?",
    ],
)
def test_off_topic_questions_are_stopped(settings: Settings, question: str) -> None:
    assert out_of_scope_topic(question, settings.out_of_scope_terms, settings.in_scope_terms)


@pytest.mark.parametrize(
    "question",
    [
        "Which New England airports are strong expansion candidates?",
        "Compare delays at LAX and SNA",
        "And BOS?",
    ],
)
def test_airport_questions_pass(settings: Settings, question: str) -> None:
    assert (
        out_of_scope_topic(question, settings.out_of_scope_terms, settings.in_scope_terms) is None
    )


def test_an_in_scope_word_keeps_a_mixed_question(settings: Settings) -> None:
    question = "Does bad weather explain the delays at SEA?"

    assert (
        out_of_scope_topic(question, settings.out_of_scope_terms, settings.in_scope_terms) is None
    )
