"""Single source for thresholds, weights and model ids (DRY: no magic numbers elsewhere)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


class Settings(BaseModel):
    long_haul_min_miles: float = Field(gt=0)
    new_england_states: list[str]
    score_weights: dict[str, float]
    min_confidence: float = Field(ge=0, le=1)
    router_model: str
    judge_model: str


def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
