from hypothesis import given
from hypothesis import strategies as st

from airport_agent.display import percent
from airport_agent.tools.common import metric


@given(st.floats(min_value=0, max_value=1))
def test_the_quoted_text_is_built_from_the_rounded_value(value: float) -> None:
    result = metric(value, percent)

    assert result is not None
    assert result["shown"] == percent(float(str(result["value"])))


def test_a_missing_value_stays_missing() -> None:
    assert metric(None, percent) is None
