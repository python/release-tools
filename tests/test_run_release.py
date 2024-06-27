import pytest

import run_release


@pytest.mark.parametrize(
    ["url", "expected"],
    [
        ("github.com/hugovk/cpython.git", "hugovk"),
        ("git@github.com:hugovk/cpython.git", "hugovk"),
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
