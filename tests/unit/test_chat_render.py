import json

import pytest

from airport_agent.answer import Status
from airport_agent.chat_models import NotesOut
from airport_agent.chat_render import (
    GENERIC_ERROR,
    STATUS_LABELS,
    escape_markdown,
    notes_markdown,
    reply_from_response,
    status_label,
)

BODY = {
    "session_id": "a" * 32,
    "status": "answered",
    "text": "BOS scores 62.2.",
    "notes": {"assumptions": ["A"], "caveats": [], "sources": ["S1", "S2"]},
}


def test_notes_always_show_all_three_sections() -> None:
    text = notes_markdown(NotesOut(assumptions=["A"], caveats=[], sources=["S1", "S2"]))

    assert (
        text.index("**Assumptions**") < text.index("**Caveats**") < text.index("**Data sources**")
    )
    assert "- A" in text
    assert "**Caveats**\n- none" in text
    assert "- S1\n- S2" in text


def test_dollar_signs_are_escaped_so_amounts_are_not_read_as_formulas() -> None:
    assert escape_markdown("between $5M and $10M") == "between \\$5M and \\$10M"
    assert "\\$" in notes_markdown(NotesOut(assumptions=["$1"], caveats=[], sources=[]))


def test_only_answers_that_are_not_plain_get_a_warning() -> None:
    assert status_label("answered") == ""
    assert "could not be verified" in status_label("data_only")
    assert "could not be verified" in status_label("abstained")
    assert "could not be reached" in status_label("unavailable")
    assert status_label("x") == "Unexpected status: x"


def test_a_reply_without_a_session_keeps_the_page_on_its_current_one() -> None:
    body = {**BODY, "session_id": None, "status": "unavailable"}

    reply = reply_from_response(200, json.dumps(body))

    assert reply.session_id is None
    assert not reply.reset_session


def test_a_good_reply_carries_the_text_notes_label_and_session() -> None:
    reply = reply_from_response(200, json.dumps({**BODY, "status": "abstained"}))

    assert reply.text == "BOS scores 62.2."
    assert reply.session_id == "a" * 32
    assert "No answer shown" in reply.label
    assert "**Data sources**" in reply.notes
    assert not reply.reset_session


@pytest.mark.parametrize(
    "body", ["", "<html>proxy</html>", "[]", json.dumps({"text": "no session or notes"})]
)
def test_a_200_that_is_not_our_reply_reads_as_a_server_error(body: str) -> None:
    reply = reply_from_response(200, body)

    assert reply.text == GENERIC_ERROR
    assert reply.session_id is None


def test_an_expired_conversation_resets_the_session() -> None:
    reply = reply_from_response(404, json.dumps({"detail": "unknown or expired session"}))

    assert "expired" in reply.text
    assert reply.reset_session


def test_a_too_long_question_shows_the_limit_not_json() -> None:
    reply = reply_from_response(
        413, json.dumps({"detail": "message is longer than 2000 characters"})
    )

    assert reply.text == "That question is too long. message is longer than 2000 characters"
    assert "{" not in reply.text
    assert reply_from_response(413, "not json").text == "That question is too long."


def test_a_rejected_message_says_so_instead_of_blaming_the_server() -> None:
    reply = reply_from_response(422, json.dumps({"detail": [{"msg": "String should have..."}]}))

    assert "could not be sent" in reply.text
    assert reply.text != GENERIC_ERROR


def test_other_failures_are_plain_messages() -> None:
    assert "try again" in reply_from_response(500, "Traceback ...").text
    assert "Is the API running" in reply_from_response(0, "").text


@pytest.mark.parametrize("status", ["unavailable", "abstained"])
def test_a_reply_without_an_answer_shows_its_text_once_with_no_label_and_no_notes(
    status: str,
) -> None:
    empty = {"assumptions": [], "caveats": [], "sources": []}
    body = {
        **BODY,
        "status": status,
        "text": "The assistant is unavailable.",
        "notes": empty,
    }

    reply = reply_from_response(200, json.dumps(body))

    assert reply.text == "The assistant is unavailable."
    assert reply.label == ""
    assert reply.notes == ""


def test_an_answer_with_empty_notes_still_shows_all_three_sections() -> None:
    empty = {"assumptions": [], "caveats": [], "sources": []}

    shown = reply_from_response(200, json.dumps({**BODY, "notes": empty})).notes

    assert "**Assumptions**\n- none" in shown
    assert "**Data sources**\n- none" in shown


def test_every_status_has_a_label() -> None:
    assert set(STATUS_LABELS) == {s.value for s in Status}
