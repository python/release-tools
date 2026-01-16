import builtins
import contextlib
import io
import tarfile
from contextlib import nullcontext as does_not_raise
from pathlib import Path
from typing import cast

import pytest

import run_release
from release import ReleaseShelf, Tag
from run_release import ReleaseException


@pytest.mark.parametrize(
    "version",
    ["sigstore 3.6.2", "sigstore 3.6.6"],
)
def test_check_sigstore_version_success(version) -> None:
    # Verify runs with no exceptions
    run_release.check_sigstore_version(version)


@pytest.mark.parametrize(
    "version",
    ["sigstore 3.4.0", "sigstore 3.6.0", "sigstore 4.0.0", ""],
)
def test_check_sigstore_version_exception(version) -> None:
    with pytest.raises(
        ReleaseException, match="Sigstore version not detected or not valid"
    ):
        run_release.check_sigstore_version(version)


@pytest.mark.parametrize(
    ["url", "expected"],
    [
        ("github.com/hugovk/cpython.git", "hugovk"),
        ("git@github.com:hugovk/cpython.git", "hugovk"),
        ("https://github.com/hugovk/cpython.git", "hugovk"),
    ],
)
def test_extract_github_owner(url: str, expected: str) -> None:
    assert run_release.extract_github_owner(url) == expected


def test_invalid_extract_github_owner() -> None:
    with pytest.raises(
        ReleaseException,
        match="Could not parse GitHub owner from 'origin' remote URL: "
        "https://example.com",
    ):
        run_release.extract_github_owner("https://example.com")


@pytest.mark.parametrize(
    ["release_tag", "git_current_branch", "expectation"],
    [
        # Success cases
        ("3.15.0rc1", "3.15\n", does_not_raise()),
        ("3.15.0b1", "3.15\n", does_not_raise()),
        ("3.15.0a6", "main\n", does_not_raise()),
        ("3.14.3", "3.14\n", does_not_raise()),
        ("3.13.12", "3.13\n", does_not_raise()),
        # Failure cases
        (
            "3.15.0rc1",
            "main\n",
            pytest.raises(ReleaseException, match="on main branch, expected 3.15"),
        ),
        (
            "3.15.0b1",
            "main\n",
            pytest.raises(ReleaseException, match="on main branch, expected 3.15"),
        ),
        (
            "3.15.0a6",
            "3.14\n",
            pytest.raises(ReleaseException, match="on 3.14 branch, expected main"),
        ),
        (
            "3.14.3",
            "main\n",
            pytest.raises(ReleaseException, match="on main branch, expected 3.14"),
        ),
    ],
)
def test_check_cpython_repo_branch(
    monkeypatch, release_tag: str, git_current_branch: str, expectation
) -> None:
    # Arrange
    db = {"release": Tag(release_tag), "git_repo": "/fake/repo"}
    monkeypatch.setattr(
        run_release.subprocess,
        "check_output",
        lambda *args, **kwargs: git_current_branch,
    )

    # Act / Assert
    with expectation:
        run_release.check_cpython_repo_branch(cast(ReleaseShelf, db))


def test_check_magic_number() -> None:
    db = {
        "release": Tag("3.14.0rc1"),
        "git_repo": str(Path(__file__).parent / "magicdata"),
    }
    with pytest.raises(ReleaseException, match="Magic numbers in .* don't match"):
        run_release.check_magic_number(cast(ReleaseShelf, db))


def prepare_fake_docs(tmp_path: Path, content: str) -> None:
    docs_path = tmp_path / "3.13.0rc1/docs"
    docs_path.mkdir(parents=True)
    tarball = tarfile.open(docs_path / "python-3.13.0rc1-docs-html.tar.bz2", "w:bz2")
    with tarball:
        tarinfo = tarfile.TarInfo("index.html")
        tarinfo.size = len(content)
        tarball.addfile(tarinfo, io.BytesIO(content.encode()))


@contextlib.contextmanager
def fake_answers(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    """Monkey-patch input() to give the given answers. All must be consumed."""

    answers_left = list(answers)

    def fake_input(question):
        print(question, "--", answers_left[0])
        return answers_left.pop(0)

    with monkeypatch.context() as ctx:
        ctx.setattr(builtins, "input", fake_input)
        yield
    assert answers_left == []


def test_check_doc_unreleased_version_no_file(tmp_path: Path) -> None:
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    with pytest.raises(AssertionError):
        # There should be a docs artefact available
        run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_no_file_alpha(tmp_path: Path) -> None:
    db = {
        "release": Tag("3.13.0a1"),
        "git_repo": str(tmp_path),
    }
    # No docs artefact needed for alphas
    run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_ok(tmp_path: Path) -> None:
    prepare_fake_docs(
        tmp_path,
        "<div>New in 3.13</div>",
    )
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_not_ok(monkeypatch, tmp_path: Path) -> None:
    prepare_fake_docs(
        tmp_path,
        "<div>New in 3.13.0rc1 (unreleased)</div>",
    )
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    with fake_answers(monkeypatch, ["no"]), pytest.raises(AssertionError):
        run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_check_doc_unreleased_version_waived(monkeypatch, tmp_path: Path) -> None:
    prepare_fake_docs(
        tmp_path,
        "<div>New in 3.13.0rc1 (unreleased)</div>",
    )
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(tmp_path),
    }
    with fake_answers(monkeypatch, ["yes"]):
        run_release.check_doc_unreleased_version(cast(ReleaseShelf, db))


def test_update_whatsnew_toctree(tmp_path: Path) -> None:
    # Arrange
    # Only first beta triggers update
    db = {"release": Tag("3.14.0b1")}

    original_toctree_file = Path(__file__).parent / "whatsnew_index.rst"
    toctree__file = tmp_path / "patchlevel.h"
    toctree__file.write_text(original_toctree_file.read_text())

    # Act
    run_release.update_whatsnew_toctree(cast(ReleaseShelf, db), str(toctree__file))

    # Assert
    new_contents = toctree__file.read_text()
    assert "   3.15.rst\n   3.14.rst\n" in new_contents
