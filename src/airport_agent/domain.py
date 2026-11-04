"""Core value objects shared across layers."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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


class Airport(BaseModel):
    model_config = ConfigDict(frozen=True)

    iata: str
    name: str
    state: str
    latitude: float
    longitude: float
    size: str
