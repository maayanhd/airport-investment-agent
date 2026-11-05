"""Scope gate: questions this data can never answer get no model call."""

from collections.abc import Sequence

from airport_agent.domain import search_words


def _mentions(question_words: str, terms: Sequence[str]) -> str | None:
    padded = f" {question_words} "

    return next((t for t in terms if f" {' '.join(search_words(t))} " in padded), None)


def out_of_scope_topic(
    question: str, out_of_scope_terms: Sequence[str], in_scope_terms: Sequence[str]
) -> str | None:
    """The off-topic term found, or None. An in-scope term wins, so mixed questions go on."""
    words = " ".join(search_words(question))

    if _mentions(words, in_scope_terms):
        return None

    return _mentions(words, out_of_scope_terms)
