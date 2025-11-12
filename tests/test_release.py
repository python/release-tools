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
    ["test_tag", "expected_version", "expected_underline"],
    [
        (
            "3.14.0a6",
            "This is Python version 3.14.0 alpha 6",
            "=====================================",
        ),
        (
            "3.14.0b2",
            "This is Python version 3.14.0 beta 2",
            "====================================",
        ),
        (
            "3.14.0rc2",
            "This is Python version 3.14.0 release candidate 2",
            "=================================================",
        ),
        (
            "3.14.1",
            "This is Python version 3.14.1",
            "=============================",
        ),
    ],
)
def test_tweak_readme(
    tmp_path: Path, test_tag: str, expected_version: str, expected_underline: str
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
    original_lines = original_contents.split("\n")
    new_contents = readme_file.read_text()
    new_lines = new_contents.split("\n")
    assert new_lines[0] == expected_version
    assert new_lines[1] == expected_underline
    assert new_lines[2:] == original_lines[2:]
    assert original_contents.endswith("\n")
    assert new_contents.endswith("\n")
