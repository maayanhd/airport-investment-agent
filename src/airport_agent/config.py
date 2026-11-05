"""Single source for thresholds, weights and model ids (DRY: no magic numbers elsewhere)."""

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from airport_agent.domain import IATA_PATTERN, STATE_PATTERN, search_words
from airport_agent.scoring import COMPONENTS

WEIGHT_TOLERANCE = 1e-9
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


class Settings(BaseModel):
    long_haul_min_miles: float = Field(gt=0)
    regions: dict[str, list[str]]
    strong_signal_scaled: float = Field(gt=0, le=1)
    score_weights: dict[str, float]
    score_bounds: dict[str, tuple[float, float]]
    min_confidence: float = Field(ge=0, le=1)
    max_plain_count: int = Field(ge=0)
    max_tool_rounds: int = Field(ge=1)
    remembered_airports: int = Field(ge=1)
    fallback_max_lines: int = Field(ge=1)
    history_turns: int = Field(ge=1)
    validation_retries: int = Field(ge=0)
    year_range: tuple[int, int]
    router_model: str
    judge_model: str
    coverage_airports: list[str]
    airport_aliases: dict[str, list[str]]
    snapshot_dir: Path
    max_data_age_months: int = Field(ge=1)
    traffic_history_months: int = Field(ge=13)
    traffic_min_months: int = Field(ge=13)
    delays_months: int = Field(ge=3)
    delays_min_months: int = Field(ge=3)
    min_airport_share: float = Field(gt=0, le=1)
    max_name_candidates: int = Field(ge=2)

    @field_validator("coverage_airports")
    @classmethod
    def _valid_unique_codes(cls, codes: list[str]) -> list[str]:
        if len(set(codes)) != len(codes) or any(not re.fullmatch(IATA_PATTERN, c) for c in codes):
            raise ValueError("coverage_airports must be unique 3-letter uppercase IATA codes")
        return codes

    @field_validator("regions")
    @classmethod
    def _regions_are_named_states(cls, regions: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, states in regions.items():
            valid = states and all(re.fullmatch(STATE_PATTERN, s) for s in states)

            if name != " ".join(search_words(name)) or not valid:
                raise ValueError(f"region {name!r}: use a lower-case name and 2-letter states")

        return regions

    @model_validator(mode="after")
    def _window_covers_minimum(self) -> Self:
        if self.traffic_history_months < self.traffic_min_months:
            raise ValueError("traffic_history_months must be at least traffic_min_months")

        if self.delays_months < self.delays_min_months:
            raise ValueError("delays_months must be at least delays_min_months")

        return self

    @model_validator(mode="after")
    def _aliases_point_at_covered_airports(self) -> Self:
        for alias, codes in self.airport_aliases.items():
            if (
                alias != " ".join(search_words(alias))
                or not codes
                or set(codes) - set(self.coverage_airports)
            ):
                raise ValueError(f"airport_aliases[{alias!r}] must list covered airport codes")

        return self

    @model_validator(mode="after")
    def _scoring_is_consistent(self) -> Self:
        if self.score_weights.keys() != self.score_bounds.keys():
            raise ValueError("score_weights and score_bounds must name the same components")

        if self.score_weights.keys() - COMPONENTS.keys():
            raise ValueError(
                f"unknown score components: {sorted(self.score_weights.keys() - COMPONENTS.keys())}"
            )

        if any(weight < 0 for weight in self.score_weights.values()):
            raise ValueError("score_weights must not be negative")

        if abs(sum(self.score_weights.values()) - 1) > WEIGHT_TOLERANCE:
            raise ValueError("score_weights must sum to 1")

        if any(low >= high for low, high in self.score_bounds.values()):
            raise ValueError("each score_bounds entry needs low < high")

        return self


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
