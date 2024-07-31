import pytest

import size


@pytest.mark.parametrize(
    ["filename", "expected"],
    [
        ("file.tgz", False),
        ("file.tar.bz2", False),
        ("file.tar.xz", False),
        ("file.pdb.zip", False),
        ("file.amd64.msi", False),
        ("file.msi", False),
        ("file.dmg", False),
        ("file.ext", True),
    ],
)
def test_ignore(filename: str, expected: bool) -> None:
    assert size.ignore(filename) is expected


@pytest.mark.parametrize(
    ["filename", "expected"],
    [
        ("file.tgz", 0),
        ("file.tar.bz2", 1),
        ("file.tar.xz", 2),
        ("file.pdb.zip", 3),
        ("file.amd64.msi", 4),
        ("file.msi", 5),
        ("file.dmg", 6),
        ("file.ext", 9999),
    ],
)
def test_key(filename: str, expected: int) -> None:
    assert size.key(filename) == expected
