"""The tools' own figures as plain text, for when the model's wording is not verified. Pure."""

from collections.abc import Iterable, Iterator, Sequence

from airport_agent.evidence import owner_of
from airport_agent.tools.result import ToolResult

MAX_LINES = 40
HIDDEN_KEYS = {"iata"}  # the airport's code is the label of its rows instead


def _lines(node: object, label: str) -> Iterator[str]:
    if isinstance(node, dict) and "shown" in node:
        yield f"{label}: {node['shown']}"
    elif isinstance(node, dict):
        owner = owner_of(node)
        base = f"{label} / {owner}" if owner and owner not in label else label

        for key, value in node.items():
            if key not in HIDDEN_KEYS:
                yield from _lines(value, f"{base} / {key}" if base else str(key))
    elif isinstance(node, list):
        for value in node:
            yield from _lines(value, label)
    elif node not in (None, ""):
        yield f"{label}: {node}"


def _capped(lines: Iterable[str]) -> list[str]:
    everything = list(lines)

    if len(everything) <= MAX_LINES:
        return everything

    omitted = len(everything) - MAX_LINES

    return [*everything[:MAX_LINES], f"... {omitted} more values not shown"]


def figures_text(results: Sequence[ToolResult]) -> str:
    """One line per value, labelled by tool and field, exactly as the tool gave it."""
    lines = [line for r in results if r.ok for line in _lines(r.data, r.tool)]

    return "\n".join(f"- {line}" for line in _capped(lines))
