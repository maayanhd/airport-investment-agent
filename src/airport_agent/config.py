"""Single source for thresholds, weights and model ids (DRY: no magic numbers elsewhere)."""

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from airport_agent.domain import IATA_PATTERN

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


class Settings(BaseModel):
    long_haul_min_miles: float = Field(gt=0)
    new_england_states: list[str]
    score_weights: dict[str, float]
    min_confidence: float = Field(ge=0, le=1)
    router_model: str
    judge_model: str
    coverage_airports: list[str]
    snapshot_dir: Path
    max_data_age_months: int = Field(ge=1)
    traffic_history_months: int = Field(ge=13)
    traffic_min_months: int = Field(ge=13)
    delays_months: int = Field(ge=3)
    delays_min_months: int = Field(ge=3)
    min_airport_share: float = Field(gt=0, le=1)

    @field_validator("coverage_airports")
    @classmethod
    def _valid_unique_codes(cls, codes: list[str]) -> list[str]:
        if len(set(codes)) != len(codes) or any(not re.fullmatch(IATA_PATTERN, c) for c in codes):
            raise ValueError("coverage_airports must be unique 3-letter uppercase IATA codes")
        return codes

    @model_validator(mode="after")
    def _window_covers_minimum(self) -> Self:
        if self.traffic_history_months < self.traffic_min_months:
            raise ValueError("traffic_history_months must be at least traffic_min_months")

        if self.delays_months < self.delays_min_months:
            raise ValueError("delays_months must be at least delays_min_months")

        return self


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
