from pathlib import Path
from typing import cast

import pytest
from pytest_mock import MockerFixture

import release


@pytest.mark.parametrize(
    ["test_editor", "expected"],
    [
        ("vim", ["vim", "README.rst"]),
        ("bbedit --wait", ["bbedit", "--wait", "README.rst"]),
    ],
)
def test_manual_edit(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    test_editor: str,
    expected: list[str],
) -> None:
    # Arrange
    monkeypatch.setenv("EDITOR", test_editor)
    mock_run_cmd = mocker.patch("release.run_cmd")

    # Act
    release.manual_edit("README.rst")

    # Assert
    mock_run_cmd.assert_called_once_with(expected)


def test_task(mocker: MockerFixture) -> None:
    # Arrange
    db = {"mock": "mock"}
    my_task = mocker.Mock()
    task = release.Task(my_task, "My task")

    # Act
    task(cast(release.ReleaseShelf, db))

    # Assert
    assert task.description == "My task"
    assert task.function == my_task
    my_task.assert_called_once_with(cast(release.ReleaseShelf, db))


@pytest.mark.parametrize(
    ["test_inputs", "expected"],
    [
        (["yes"], True),
        (["no"], False),
        (["maybe", "yes"], True),
        (["maybe", "no"], False),
        (["", "nope", "y", "yes"], True),
        (["", "nope", "n", "no"], False),
    ],
)
def test_ask_question(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    test_inputs: list[str],
    expected: bool,
) -> None:
    # Arrange
    mocker.patch("release.input", side_effect=test_inputs)

    # Act
    result = release.ask_question("Do you want to proceed?")

    # Assert
    assert result is expected
    captured = capsys.readouterr()
    assert "Do you want to proceed?" in captured.out
    # All inputs except the last are invalid
    invalid_count = len(test_inputs) - 1
    assert captured.out.count("Please enter yes or no.") == invalid_count


def test_tweak_patchlevel(tmp_path: Path) -> None:
    # Arrange
    tag = release.Tag("3.14.0b2")

    original_patchlevel_file = Path(__file__).parent / "patchlevel.h"
    patchlevel_file = tmp_path / "patchlevel.h"
    patchlevel_file.write_text(original_patchlevel_file.read_text())

    # Act
    release.tweak_patchlevel(tag, filename=str(patchlevel_file))

    # Assert
    new_contents = patchlevel_file.read_text()
    for expected in (
        "#define PY_MAJOR_VERSION        3",
        "#define PY_MINOR_VERSION        14",
        "#define PY_MICRO_VERSION        0",
        "#define PY_RELEASE_LEVEL        PY_RELEASE_LEVEL_BETA",
        "#define PY_RELEASE_SERIAL       2",
        '#define PY_VERSION              "3.14.0b2"',
    ):
        assert expected in new_contents


@pytest.mark.parametrize(
    [
        "test_tag",
        "expected_version",
        "expected_underline",
        "expected_whatsnew",
        "expected_docs",
        "expected_pep_line",
    ],
    [
        (
            "3.14.0a6",
            "This is Python version 3.14.0 alpha 6",
            "=====================================",
            "3.14 <https://docs.python.org/3.14/whatsnew/3.14.html>`_",
            "`Documentation for Python 3.14 <https://docs.python.org/3.14/>`_",
            "`PEP 745 <https://peps.python.org/pep-0745/>`__ for Python 3.14",
        ),
        (
            "3.14.0b2",
            "This is Python version 3.14.0 beta 2",
            "====================================",
            "3.14 <https://docs.python.org/3.14/whatsnew/3.14.html>`_",
            "`Documentation for Python 3.14 <https://docs.python.org/3.14/>`_",
            "`PEP 745 <https://peps.python.org/pep-0745/>`__ for Python 3.14",
        ),
        (
            "3.14.0rc2",
            "This is Python version 3.14.0 release candidate 2",
            "=================================================",
            "3.14 <https://docs.python.org/3.14/whatsnew/3.14.html>`_",
            "`Documentation for Python 3.14 <https://docs.python.org/3.14/>`_",
            "`PEP 745 <https://peps.python.org/pep-0745/>`__ for Python 3.14",
        ),
        (
            "3.15.1",
            "This is Python version 3.15.1",
            "=============================",
            "3.15 <https://docs.python.org/3.15/whatsnew/3.15.html>`_",
            "`Documentation for Python 3.15 <https://docs.python.org/3.15/>`_",
            "`PEP 790 <https://peps.python.org/pep-0790/>`__ for Python 3.15",
        ),
    ],
)
def test_tweak_readme(
    tmp_path: Path,
    test_tag: str,
    expected_version: str,
    expected_underline: str,
    expected_whatsnew: str,
    expected_docs: str,
    expected_pep_line: str,
) -> None:
    # Arrange
    tag = release.Tag(test_tag)

    original_readme_file = Path(__file__).parent / "README.rst"
    original_contents = original_readme_file.read_text()
    readme_file = tmp_path / "README.rst"
    readme_file.write_text(original_contents)

    # Act
    release.tweak_readme(tag, filename=str(readme_file))

    # Assert
    new_contents = readme_file.read_text()
    new_lines = new_contents.split("\n")
    assert new_lines[0] == expected_version
    assert new_lines[1] == expected_underline
    assert expected_whatsnew in new_contents
    assert expected_docs in new_contents
    assert expected_pep_line in new_contents
    assert original_contents.endswith("\n")
    assert new_contents.endswith("\n")
