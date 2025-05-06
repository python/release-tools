import os

import pytest

os.environ["AUTH_INFO"] = "test_username:test_api_key"

import add_to_pydotorg


@pytest.mark.parametrize(
    ["release", "expected"],
    [
        ("3.9.0a0", "390-a0"),
        ("3.10.0b3", "3100-b3"),
        ("3.11.0rc2", "3110-rc2"),
        ("3.12.15", "31215"),
    ],
)
def test_slug_for(release: str, expected: str) -> None:
    assert add_to_pydotorg.slug_for(release) == expected


def test_sigfile_for() -> None:
    assert (
        add_to_pydotorg.sigfile_for("3.14.0", "Python-3.13.0.tgz")
        == "https://www.python.org/ftp/python/3.14.0/Python-3.13.0.tgz.asc"
    )


@pytest.mark.parametrize(
    ["release", "expected"],
    [
        ("3.9.0a0", "390a0"),
        ("3.10.0b3", "3100b3"),
        ("3.11.0rc2", "3110rc2"),
        ("3.12.15", "31215"),
    ],
)
def test_make_slug(release: str, expected: str) -> None:
    assert add_to_pydotorg.make_slug(release) == expected


@pytest.mark.parametrize(
    ["release", "expected"],
    [
        ("3.9.0a0", "3.9.0"),
        ("3.10.0b3", "3.10.0"),
        ("3.11.0rc2", "3.11.0"),
        ("3.12.15", "3.12.15"),
    ],
)
def test_base_version(release: str, expected: str) -> None:
    assert add_to_pydotorg.base_version(release) == expected


@pytest.mark.parametrize(
    ["release", "expected"],
    [
        ("3.9.0a0", "3.9"),
        ("3.10.0b3", "3.10"),
        ("3.11.0rc2", "3.11"),
        ("3.12.15", "3.12"),
    ],
)
def test_minor_version(release: str, expected: str) -> None:
    assert add_to_pydotorg.minor_version(release) == expected


@pytest.mark.parametrize(
    ["release", "expected"],
    [
        ("3.9.0a0", (3, 9)),
        ("3.10.0b3", (3, 10)),
        ("3.11.0rc2", (3, 11)),
        ("3.12.15", (3, 12)),
    ],
)
def test_minor_version_tuple(release: str, expected: tuple[int, int]) -> None:
    assert add_to_pydotorg.minor_version_tuple(release) == expected
