"""Claims with no figure to look up: a direction, a rank, or which of two airports is ahead."""

import re
from collections.abc import Iterator, Mapping, Sequence

from airport_agent.evidence import Evidence
from airport_agent.numbers import Figure

CODE = r"(?P<{name}>[A-Z]{{3}})"
UP_WORDS = (
    r"grew|grows|growing|rose|rises|rising|increased|increases|gained|climbed|jumped|surged|"
    r"increase|rise|gain|(?:is|was|are|were)\s+up"
)
DOWN_WORDS = (
    r"fell|falls|falling|dropped|drops|declined|declines|decreased|decreases|shrank|shrinks|"
    r"slipped|sank|contracted|decline|drop|fall|decrease|(?:is|was|are|were)\s+down"
)
FILLER = r"(?:by\s+|of\s+)?(?:about\s+|roughly\s+|around\s+|nearly\s+|almost\s+|just\s+|over\s+)?"
UP_BEFORE = re.compile(rf"\b(?:{UP_WORDS})\s+{FILLER}$", re.IGNORECASE)
DOWN_BEFORE = re.compile(rf"\b(?:{DOWN_WORDS})\s+{FILLER}$", re.IGNORECASE)
RANK_CLAIM = re.compile(
    r"#(\d+)\b"
    r"|\brank(?:ed|s)?\s+(?:no\.?\s*|number\s*|#)?(\d+)(?:st|nd|rd|th)?\b(?!\.\d)"
    r"|\b(\d+)(?:st|nd|rd|th)\s+(?:place|position|rank|overall)\b",
    re.I,
)
TOP_CLAIM = re.compile(r"\b(?:strongest|top-ranked|highest-scoring|best candidate)\b", re.I)
# "the second strongest" and "not the strongest" are not a claim to be first
QUALIFIED_TOP = re.compile(
    r"(?:\b(?:second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|next|not|never|less|"
    r"least)\b|n't|\b\d+(?:st|nd|rd|th))\W+(?:\w+\W+){0,2}$",
    re.I,
)
# Any text up to the next sentence end; a period inside a figure ("21.4%") does not end it.
SPAN = r"(?:[^.;\n]|\.(?=\d))*?"
THAN = re.compile(
    rf"{CODE.format(name='first')}\b(?P<middle>{SPAN})\bthan\b{SPAN}\b{CODE.format(name='second')}\b"
)
OUTRANKS = re.compile(
    rf"{CODE.format(name='first')}\b{SPAN}\b(?:outranks|ranks (?:above|ahead of))\b{SPAN}\b"
    rf"{CODE.format(name='second')}\b"
)
DELAY_TOPIC = re.compile(r"\b(?:delayed|delays?|congest\w*|late)\b", re.I)
SCORE_TOPIC = re.compile(r"\bscores?\b", re.I)
RANK_TOPIC = re.compile(r"\b(?:stronger|weaker|ranks?|candidate)\b", re.I)
MORE_WORDS = re.compile(r"\b(?:more|higher|greater|stronger)\b", re.I)
LESS_WORDS = re.compile(r"\b(?:less|fewer|lower|weaker|smaller)\b", re.I)
# "better" and "worse" point opposite ways for delays (fewer is better) and for candidates
BETTER = re.compile(r"\bbetter\b", re.I)
WORSE = re.compile(r"\bworse\b", re.I)


def direction_problem(figure: Figure, evidence: Evidence) -> str | None:
    """A rise or fall stated for a figure whose only sign in the tools is the opposite one."""
    is_only_negative = figure.value in evidence.negative and figure.value not in evidence.positive
    is_only_positive = figure.value in evidence.positive and figure.value not in evidence.negative

    is_rise = UP_BEFORE.search(figure.before) is not None
    is_fall = DOWN_BEFORE.search(figure.before) is not None

    if figure.sign == 0 and not (is_rise or is_fall):
        return None

    if figure.sign < 0 and figure.value not in evidence.negative:
        return f"-{figure.text} (the tools give it without a minus)"

    if figure.sign > 0 and figure.value not in evidence.positive:
        return f"+{figure.text} (the tools give it as negative)"

    if figure.sign == 0 and is_rise and is_only_negative:
        return f"{figure.text} (the tools give it as a fall)"

    if figure.sign == 0 and is_fall and is_only_positive:
        return f"{figure.text} (the tools give it as a rise)"

    return None


def _claimed_ranks(sentence: str) -> Iterator[int]:
    for match in RANK_CLAIM.finditer(sentence):
        yield int(match[1] or match[2] or match[3])


def _rank_problems(
    sentence: str, codes: list[str], rankings: Sequence[Mapping[str, int]]
) -> Iterator[str]:
    """A rank claim in a sentence about one airport must match a rank a tool gave it."""
    if len(set(codes)) != 1:
        return

    code = codes[0]
    possible = {ranking[code] for ranking in rankings if code in ranking}

    if not possible:
        return

    for claimed in _claimed_ranks(sentence):
        if claimed not in possible:
            yield f"{code} ranked {claimed}"

    if _claims_to_be_first(sentence) and 1 not in possible:
        yield f"{code} called the strongest"


def _claims_to_be_first(sentence: str) -> bool:
    return any(
        not QUALIFIED_TOP.search(sentence[: m.start()]) for m in TOP_CLAIM.finditer(sentence)
    )


def _ahead_in_rankings(first: str, second: str, rankings: Sequence[Mapping[str, int]]) -> set[bool]:
    """Whether `first` is ahead of `second`, in each ranking that has both."""
    return {r[first] < r[second] for r in rankings if first in r and second in r}


def _ahead_in_scores(first: str, second: str, scores: Sequence[Mapping[str, float]]) -> set[bool]:
    """Whether `first` has the higher score, which can differ from its rank (partial scores)."""
    return {s[first] > s[second] for s in scores if first in s and second in s}


def _middle_direction(middle: str, *, delay_topic: bool) -> int:
    """+1 when the first airport is claimed to be ahead on the topic, -1 behind, 0 unknown."""
    if MORE_WORDS.search(middle):
        return 1

    if LESS_WORDS.search(middle):
        return -1

    if BETTER.search(middle):
        return -1 if delay_topic else 1

    if WORSE.search(middle):
        return 1 if delay_topic else -1

    return 0


def _than_problem(match: re.Match[str], evidence: Evidence) -> str | None:
    middle = match["middle"]
    first, second = match["first"], match["second"]
    is_delay = DELAY_TOPIC.search(middle) is not None
    direction = _middle_direction(middle, delay_topic=is_delay)

    if direction == 0 or first == second:
        return None

    if is_delay:
        if frozenset((first, second)) in evidence.tied:
            return match.group(0)

        order = (first, second) if direction > 0 else (second, first)
        known = {(first, second), (second, first)} & evidence.delay_order

        return None if not known or order in known else match.group(0)

    if SCORE_TOPIC.search(middle):
        verdicts = _ahead_in_scores(first, second, evidence.scores)

        return None if not verdicts or (direction > 0) in verdicts else match.group(0)

    if RANK_TOPIC.search(middle):
        verdicts = _ahead_in_rankings(first, second, evidence.rankings)

        return None if not verdicts or (direction > 0) in verdicts else match.group(0)

    return None


def _outranks_problem(match: re.Match[str], evidence: Evidence) -> str | None:
    verdicts = _ahead_in_rankings(match["first"], match["second"], evidence.rankings)

    return None if not verdicts or True in verdicts else match.group(0)


def comparison_problems(sentence: str, evidence: Evidence) -> list[str]:
    """Which of two airports is ahead, checked against the order a tool returned."""
    problems = [_than_problem(m, evidence) for m in THAN.finditer(sentence)]
    problems += [_outranks_problem(m, evidence) for m in OUTRANKS.finditer(sentence)]

    return [p for p in problems if p]


def claim_problems(sentence: str, codes: list[str], evidence: Evidence) -> list[str]:
    ranks = _rank_problems(sentence, codes, evidence.rankings)

    return [*ranks, *comparison_problems(sentence, evidence)]
