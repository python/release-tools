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
