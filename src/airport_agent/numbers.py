"""Finding numbers in text, the way tool results and answers both write them. Pure."""

import re
from dataclasses import dataclass
from decimal import Decimal

DATES = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2})?\b"
    r"|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.? (?:19|20)\d{2}\b"
    r"|\b\d+(?:st|nd|rd|th)\b"
)
UNITS = (
    r"percentage points?|percent|per cent|pp|pts?|points?|miles?|minutes?|min|hours?|days?|"
    r"years?|million|billion|thousand"
)
NUMBER = re.compile(
    r"(?<![\w.])(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"(?P<unit>\s?%|\s?(?:{UNITS})\b|[MKB]\b)?"
)
NUMBER_WORDS = re.compile(
    r"(?<!\d )(?<!\d)\b(?:eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|dozens?|hundreds?|thousands?|millions?|"
    r"billions?|half|halves|quarters?|(?:a|one|two) thirds?|double[sd]?|doubling|triple[sd]?|"
    r"tripling|twice)\b|[½¼¾⅓⅔]",
    re.IGNORECASE,
)
SIGNS = {"-": -1, "−": -1, "+": 1}  # a hyphen inside "5-10" is a range, not a sign
CONTEXT_CHARS = 40


@dataclass(frozen=True)
class Figure:
    value: Decimal  # the magnitude
    sign: int  # -1 or 1 when the text wrote a sign, else 0
    unit: str
    has_separator: bool  # written with a comma or a decimal point
    text: str  # as written, without the sign
    before: str  # the text just ahead of it, to read a direction word


def to_decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", "")).normalize()


def _sign_before(plain: str, start: int) -> int:
    if start == 0 or plain[start - 1] not in SIGNS:
        return 0

    if plain[max(0, start - 2) : start - 1].isalnum():
        return 0

    return SIGNS[plain[start - 1]]


def _figure(plain: str, match: re.Match[str]) -> Figure:
    start = match.start()

    return Figure(
        value=to_decimal(match["number"]),
        sign=_sign_before(plain, start),
        unit=match["unit"] or "",
        has_separator=re.search(r"[.,]", match["number"]) is not None,
        text=match.group(0).strip(),
        before=plain[max(0, start - CONTEXT_CHARS) : start],
    )


def find_figures(text: str) -> list[Figure]:
    """Dates and ordinals are blanked first, so their digits are never read as figures."""
    plain = DATES.sub(" ", text)

    return [_figure(plain, match) for match in NUMBER.finditer(plain)]
