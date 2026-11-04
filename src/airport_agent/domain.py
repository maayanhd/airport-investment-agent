"""Core value objects shared across layers."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IATA_PATTERN = r"[A-Z]{3}"
STATE_PATTERN = r"[A-Z]{2}"


class DataMode(StrEnum):
    LIVE = "live"
    SNAPSHOT = "snapshot"


class Provenance(BaseModel):
    """Where a piece of data came from. Attached to every tool result."""

    model_config = ConfigDict(frozen=True)

    source: str
    mode: DataMode
    fetched_at: datetime
    as_of: str = ""
    note: str = ""  # why a snapshot was used instead of live data, if it was


class Airport(BaseModel):
    model_config = ConfigDict(frozen=True)

    iata: str = Field(pattern=f"^{IATA_PATTERN}$")
    name: str
    state: str = Field(pattern=f"^{STATE_PATTERN}$")
    latitude: float
    longitude: float
    size: Literal["large", "medium", "small"]
