"""The JSON the chat API accepts and returns, shared by the server and the chat page."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

SESSION_ID = r"^[0-9a-f]{32}$"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: Annotated[str, Field(min_length=1)]  # blank after stripping is rejected
    session_id: Annotated[str | None, Field(pattern=SESSION_ID)] = None  # none starts a new one


class NotesOut(BaseModel):
    assumptions: list[str]
    caveats: list[str]
    sources: list[str]


class ChatResponse(BaseModel):
    session_id: str | None  # none when no conversation was kept, e.g. the model was unreachable
    status: str
    text: str
    notes: NotesOut
