"""The question set: what to ask, which tools should answer, what the answer must state."""

from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"


class Category(StrEnum):
    RANK = "rank"
    COMPARE = "compare"
    SHARE = "share"
    UNMET_DEMAND = "unmet_demand"
    FOLLOW_UP = "follow_up"
    OUT_OF_SCOPE = "out_of_scope"
    ADVERSARIAL = "adversarial"


class Expect(StrEnum):
    ANSWER = "answer"  # at least one tool succeeds and the reply rests on it
    ABSTAIN = "abstain"  # no tool succeeds: the agent asks, declines or says it is not covered


class Disclosure(StrEnum):
    DATA_MONTH = "data_month"
    DATA_MODE = "data_mode"  # live or snapshot
    PROXY_LABEL = "proxy_label"
    ASSUMPTIONS = "assumptions"
    ALIAS_READING = "alias_reading"  # 'LA' read as LAX
    NOT_A_SHARE = "not_a_share"


class _Strict(BaseModel):
    """A misspelled key in questions.yaml is an error, not a check that quietly stops running."""

    model_config = ConfigDict(extra="forbid")


class ExpectedCall(_Strict):
    tool: str
    args: dict[str, Any]  # JSON arguments, validated by the tool itself


class Turn(_Strict):
    question: str
    expect: Expect = Expect.ANSWER
    calls: list[ExpectedCall] = Field(default_factory=list)
    must_state: list[Disclosure] = Field(default_factory=list)
    notes: str = ""


class Case(_Strict):
    id: str
    category: Category
    turns: list[Turn] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _single_turn_shorthand(cls, raw: Any) -> Any:
        """A case with a `question` key is one turn written inline."""
        if not isinstance(raw, dict) or "question" not in raw:
            return raw

        if "turns" in raw:
            raise ValueError("a case has either `question` or `turns`, not both")

        turn_keys = ("question", "expect", "calls", "must_state")
        turn = {key: raw[key] for key in turn_keys if key in raw}
        rest = {key: value for key, value in raw.items() if key not in turn_keys}

        return {**rest, "turns": [turn]}


def check_unique(cases: list[Case]) -> list[Case]:
    """Ids and questions are keys elsewhere (the oracle finds its script by question)."""
    ids = [case.id for case in cases]
    questions = [turn.question for case in cases for turn in case.turns]

    if len(set(ids)) != len(ids) or len(set(questions)) != len(questions):
        raise ValueError("case ids and questions must be unique")

    return cases


def load_cases(path: Path = QUESTIONS_PATH) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    return check_unique([Case.model_validate(item) for item in raw])
