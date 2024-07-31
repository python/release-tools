from pathlib import Path
from typing import cast

import pytest

import run_release
from release import ReleaseShelf, Tag


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
        run_release.ReleaseException,
        match="Could not parse GitHub owner from 'origin' remote URL: "
        "https://example.com",
    ):
        run_release.extract_github_owner("https://example.com")


def test_check_magic_number() -> None:
    db = {
        "release": Tag("3.13.0rc1"),
        "git_repo": str(Path(__file__).parent / "magicdata"),
    }
    with pytest.raises(
        run_release.ReleaseException, match="Magic numbers in .* don't match"
    ):
        run_release.check_magic_number(cast(ReleaseShelf, db))
