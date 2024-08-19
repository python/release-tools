from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import release


def test_tag() -> None:
    # Arrange
    tag_name = "3.12.2"

    # Act
    tag = release.Tag(tag_name)

    # Assert
    assert str(tag) == "3.12.2"
    assert str(tag.next_minor_release()) == "3.13.0a0"
    assert tag.as_tuple() == (3, 12, 2, "f", 0)
    assert tag.branch == "3.12"
    assert tag.gitname == "v3.12.2"
    assert tag.is_alpha_release is False
    assert tag.is_feature_freeze_release is False
    assert tag.is_release_candidate is False
    assert tag.nickname == "3122"
    assert tag.normalized() == "3.12.2"


def test_tag_phase() -> None:
    # Arrange
    alpha = release.Tag("3.13.0a7")
    beta1 = release.Tag("3.13.0b1")
    beta4 = release.Tag("3.13.0b4")
    rc = release.Tag("3.13.0rc3")

    # Act / Assert
    assert alpha.is_alpha_release is True
    assert alpha.is_feature_freeze_release is False
    assert alpha.is_release_candidate is False

    assert beta1.is_alpha_release is False
    assert beta1.is_feature_freeze_release is True
    assert beta1.is_release_candidate is False

    assert beta4.is_alpha_release is False
    assert beta4.is_feature_freeze_release is False
    assert beta4.is_release_candidate is False

    assert rc.is_alpha_release is False
    assert rc.is_feature_freeze_release is False
    assert rc.is_release_candidate is True


def test_tag_committed_at_not_found() -> None:
    # Arrange
    tag = release.Tag("3.12.2")

    # Act / Assert
    with pytest.raises(SystemExit):
        tag.committed_at()


def test_tag_committed(mocker: MockerFixture) -> None:
    # Arrange
    tag = release.Tag("3.12.2")

    proc = CompletedProcess([], 0)
    proc.stdout = b"1707250784"
    mocker.patch("subprocess.run", return_value=proc)

    # Act / Assert
    assert str(tag.committed_at) == "2024-02-06 20:19:44+00:00"


def test_tag_dot(mocker: MockerFixture) -> None:
    # Arrange
    tag_name = "."
    mocker.patch("os.getcwd", return_value="/path/to/3.12.2")

    # Act
    tag = release.Tag(tag_name)

    # Assert
    assert str(tag) == "3.12.2"


def test_tag_invalid() -> None:
    # Arrange
    tag_name = "bleep"

    # Act / Assert
    with pytest.raises(SystemExit):
        release.Tag(tag_name)


def test_tag_docs_attributes() -> None:
    # Arrange
    alpha = release.Tag("3.13.0a7")
    beta = release.Tag("3.13.0b1")
    rc = release.Tag("3.13.0rc3")
    final_zero = release.Tag("3.13.0")
    final_3 = release.Tag("3.13.3")

    # Act / Assert
    assert alpha.includes_docs is False
    assert beta.includes_docs is False
    assert rc.includes_docs is True
    assert final_zero.includes_docs is True
    assert final_3.includes_docs is True

    assert alpha.doc_version == "3.13"
    assert beta.doc_version == "3.13"
    assert rc.doc_version == "3.13"
    assert final_zero.doc_version == "3.13"
    assert final_3.doc_version == "3.13.3"
