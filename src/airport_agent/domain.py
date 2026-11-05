"""Core value objects shared across layers."""

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IATA_PATTERN = r"[A-Z]{3}"
STATE_PATTERN = r"[A-Z]{2}"
MAX_NAME_CHARS = 80


def search_words(text: str) -> list[str]:
    """Lower-case words with punctuation removed, so "L.A." and "la" are comparable."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


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
    stale: bool = False  # the data month is older than the configured limit
    save_failed: bool = False  # live data was good but could not be written to the snapshot


class Airport(BaseModel):
    model_config = ConfigDict(frozen=True)

    iata: str = Field(pattern=f"^{IATA_PATTERN}$")
    name: str
    city: str  # where the airport is, as OurAirports lists it; may differ from the name
    state: str = Field(pattern=f"^{STATE_PATTERN}$")
    latitude: float
    longitude: float
    size: Literal["large", "medium", "small"]

    @field_validator("name", "city")
    @classmethod
    def _plain_text(cls, text: str) -> str:
        """Drop markup-like characters and cap the length. Words survive, so this does not stop
        text that reads like an instruction; tool output is treated as data, never as orders."""
        return " ".join(re.sub(r"[^\w\s.,'&()/-]", "", text).split())[:MAX_NAME_CHARS]

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, name: str) -> str:
        if not name:
            raise ValueError("airport name is empty after cleaning")

        return name


class MonthlyTraffic(BaseModel):
    model_config = ConfigDict(frozen=True)

    iata: str = Field(pattern=f"^{IATA_PATTERN}$")
    month: date
    departures: int = Field(ge=0)
    passengers: int = Field(ge=0)
    seats: int = Field(ge=0)
    load_factor: float = Field(ge=0, le=100)  # percent of seats filled
    avg_flight_distance_miles: float = Field(ge=0)


class MonthlyDelays(BaseModel):
    model_config = ConfigDict(frozen=True)

    iata: str = Field(pattern=f"^{IATA_PATTERN}$")
    month: date
    flights: int = Field(ge=0)  # scheduled flights of the reporting carriers, cancelled included
    cancelled: int = Field(ge=0)
    delayed_15: int = Field(ge=0)  # departures at least 15 minutes late
    delay_minutes: float = Field(ge=0)  # total departure delay over operated flights

    @model_validator(mode="after")
    def _counts_fit_together(self) -> Self:
        if self.cancelled + self.delayed_15 > self.flights:
            raise ValueError("cancelled plus delayed flights exceed the flight count")

        return self
