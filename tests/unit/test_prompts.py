from airport_agent.answer import build_notes
from airport_agent.dataset import Dataset
from airport_agent.prompts import entities_note
from airport_agent.tools import build_registry
from airport_agent.tools.result import ErrorKind, ToolResult


def test_the_note_names_the_tool_airports_and_region(dataset: Dataset) -> None:
    result = build_registry(dataset).call("rank_region", {"region": "New England", "top_n": 2})

    note = entities_note([result])

    assert note.startswith("Previous turn: tools rank_region; airports PSM, ORH")
    assert note.endswith("region New England.")


def test_failed_tools_leave_no_note() -> None:
    failed = ToolResult.failure("airport_score", ErrorKind.UNKNOWN_AIRPORT, "no such airport")

    assert entities_note([failed]) == ""
    assert entities_note([]) == ""


def test_notes_are_collected_once_across_tools(dataset: Dataset) -> None:
    registry = build_registry(dataset)
    results = [registry.call("airport_score", {"airport": c}) for c in ("BOS", "SFO")]

    notes = build_notes(results)

    assert len(notes.caveats) == len(set(notes.caveats))
    assert len(notes.sources) == 3  # catalog, traffic, delays: the same for both airports
