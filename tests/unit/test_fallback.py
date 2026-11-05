from airport_agent.dataset import Dataset
from airport_agent.fallback import figures_text
from airport_agent.tools import build_registry
from airport_agent.tools.result import ErrorKind, ToolResult


def test_each_value_is_one_line_labelled_by_tool_and_airport(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "BOS"})

    text = figures_text([result], 40)

    assert f"- airport_score / BOS / score: {result.data['score']}" in text
    assert all(line.startswith("- ") for line in text.splitlines())
    assert "iata" not in text


def test_a_value_with_a_shown_text_is_printed_as_the_tool_shows_it() -> None:
    result = ToolResult(tool="t", ok=True, data={"rate": {"value": 0.253, "shown": "25.3%"}})

    assert figures_text([result], 40) == "- t / rate: 25.3%"


def test_failed_results_and_empty_values_are_left_out() -> None:
    failed = ToolResult.failure("t", ErrorKind.UNKNOWN_AIRPORT, "no such airport")
    empty = ToolResult(tool="u", ok=True, data={"x": None, "y": ""})

    assert figures_text([failed, empty], 40) == ""


def test_a_long_result_is_cut_with_a_count() -> None:
    result = ToolResult(tool="t", ok=True, data={f"k{n}": n for n in range(8)})

    lines = figures_text([result], 3).splitlines()

    assert len(lines) == 4
    assert lines[-1] == "- ... 5 more values not shown"


def test_score_components_are_named_so_the_lines_can_be_told_apart(dataset: Dataset) -> None:
    result = build_registry(dataset).call("airport_score", {"airport": "ORH"})

    text = figures_text([result], 40)

    for name in ("passenger_growth", "load_factor", "delay_pressure", "capacity_gap"):
        assert f"airport_score / ORH / components / {name}: " in text


def test_bookkeeping_fields_are_not_printed(dataset: Dataset) -> None:
    result = build_registry(dataset).call("rank_region", {"region": "AK", "top_n": 2})

    text = figures_text([result], 40)

    assert "complete" not in text
    assert "states" not in text
    assert "rank_region / ranking / BET / score: " in text
