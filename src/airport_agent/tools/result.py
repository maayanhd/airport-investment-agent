"""What every tool returns. The LLM sees this and nothing else."""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field

from airport_agent.domain import Provenance


class ErrorKind(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    BAD_ARGUMENTS = "bad_arguments"
    UNKNOWN_AIRPORT = "unknown_airport"
    AMBIGUOUS_AIRPORT = "ambiguous_airport"
    NOT_COVERED = "not_covered"
    INSUFFICIENT_DATA = "insufficient_data"


class ToolError(BaseModel):
    kind: ErrorKind
    message: str
    options: list[str] = Field(default_factory=list)  # e.g. the candidates to ask the user about


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)  # readings and rules we applied
    caveats: list[str] = Field(default_factory=list)  # proxies, partial or stale data
    sources: list[Provenance] = Field(default_factory=list)
    error: ToolError | None = None

    @classmethod
    def failure(
        cls, tool: str, kind: ErrorKind, message: str, options: list[str] | None = None
    ) -> Self:
        return cls(
            tool=tool, ok=False, error=ToolError(kind=kind, message=message, options=options or [])
        )
