"""How an API reply is shown in the chat. Plain functions, so they are tested without Streamlit."""

import json
from dataclasses import dataclass

from pydantic import ValidationError

from airport_agent.answer import Status
from airport_agent.chat_models import ChatResponse, NotesOut

OK = 200
NOT_FOUND = 404
TOO_LARGE = 413
UNPROCESSABLE = 422
UNREACHABLE = 0  # no HTTP reply at all
NO_ANSWER = {Status.ABSTAINED.value, Status.UNAVAILABLE.value}  # the text says it; no extras
STATUS_LABELS = {
    Status.ANSWERED.value: "",
    Status.DATA_ONLY.value: (
        "Figures only: the written answer could not be verified, so these are the tools' values."
    ),
    Status.ABSTAINED.value: "No answer shown: it could not be verified against the data.",
    Status.UNAVAILABLE.value: "The assistant could not be reached.",
}
GENERIC_ERROR = "Something went wrong on the server. Please try again."


@dataclass(frozen=True)
class Reply:
    text: str
    label: str = ""
    notes: str = ""
    session_id: str | None = None  # the conversation to continue, when the server named one
    reset_session: bool = False  # the server no longer knows our conversation


def escape_markdown(text: str) -> str:
    """Streamlit reads two dollar signs as a formula, which would garble amounts."""
    return text.replace("$", "\\$")


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, f"Unexpected status: {status}")


def notes_markdown(notes: NotesOut) -> str:
    """Every answer shows its assumptions, caveats and sources, even when a list is empty."""
    sections = (
        ("Assumptions", notes.assumptions),
        ("Caveats", notes.caveats),
        ("Data sources", notes.sources),
    )
    parts = []

    for title, lines in sections:
        body = "\n".join(f"- {escape_markdown(line)}" for line in lines) if lines else "- none"
        parts.append(f"**{title}**\n{body}")

    return "\n\n".join(parts)


def _detail(body_text: str) -> str:
    try:
        detail = json.loads(body_text)["detail"]
    except (ValueError, KeyError, TypeError):
        return ""

    return detail if isinstance(detail, str) else ""


def _answer(body_text: str) -> Reply:
    try:
        body = ChatResponse.model_validate_json(body_text)
    except ValidationError:
        return Reply(GENERIC_ERROR)

    has_answer = body.status not in NO_ANSWER

    return Reply(
        text=escape_markdown(body.text),
        label=status_label(body.status) if has_answer else "",
        notes=notes_markdown(body.notes) if has_answer else "",
        session_id=body.session_id,
    )


def reply_from_response(status_code: int, body_text: str) -> Reply:
    """What to show for an HTTP reply; a reply of an unexpected shape reads as a server error."""
    if status_code == OK:
        return _answer(body_text)

    if status_code == NOT_FOUND:
        return Reply("This conversation expired. Ask again to start a new one.", reset_session=True)

    if status_code == TOO_LARGE:
        return Reply(f"That question is too long. {_detail(body_text)}".strip())

    if status_code == UNPROCESSABLE:
        return Reply("That message could not be sent. Type a question and try again.")

    if status_code == UNREACHABLE:
        return Reply("Could not reach the assistant. Is the API running?")

    return Reply(GENERIC_ERROR)
