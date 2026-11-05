from pathlib import Path

from airport_agent.env_file import load_env_file


def test_reads_plain_quoted_and_exported_lines(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# a comment\n\nPLAIN=one\nQUOTED=\"two\"\nexport EXPORTED='three'\nSPACED = four \n",
        encoding="utf-8",
    )
    environ: dict[str, str] = {}

    loaded = load_env_file(path, environ)

    assert environ == {"PLAIN": "one", "QUOTED": "two", "EXPORTED": "three", "SPACED": "four"}
    assert loaded == ["PLAIN", "QUOTED", "EXPORTED", "SPACED"]


def test_a_variable_already_set_wins(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("KEY=from-file\n", encoding="utf-8")
    environ = {"KEY": "from-shell"}

    loaded = load_env_file(path, environ)

    assert environ == {"KEY": "from-shell"}
    assert loaded == []


def test_a_missing_file_changes_nothing(tmp_path: Path) -> None:
    environ: dict[str, str] = {}

    assert load_env_file(tmp_path / "nope.env", environ) == []
    assert environ == {}


def test_lines_without_a_name_or_equals_sign_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("no equals here\n=orphan\nOK=1\n", encoding="utf-8")
    environ: dict[str, str] = {}

    load_env_file(path, environ)

    assert environ == {"OK": "1"}
