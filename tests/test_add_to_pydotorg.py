import os
from pathlib import Path

import pytest
from pyfakefs.fake_filesystem import FakeFilesystem

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


def test_list_files(fs: FakeFilesystem) -> None:
    # Arrange
    fs.add_real_file("tests/fake-ftp-files.txt")
    fake_files = Path("tests/fake-ftp-files.txt").read_text().splitlines()
    for fn in fake_files:
        if fn.startswith("#"):  # comment
            continue

        file_path = Path(add_to_pydotorg.ftp_root) / "3.14.0" / fn
        if fn.endswith("/"):
            fs.create_dir(file_path)
        else:
            fs.create_file(file_path)

    # Act
    files = list(add_to_pydotorg.list_files("3.14.0b3"))

    # Assert
    assert files == [
        ("Python-3.14.0b3.tar.xz", "XZ compressed source tarball", 3, True, ""),
        ("Python-3.14.0b3.tgz", "Gzipped source tarball", 3, False, ""),
        (
            "python-3.14.0b3-amd64.exe",
            "Windows installer (64-bit)",
            1,
            True,
            "Recommended",
        ),
        (
            "python-3.14.0b3-arm64.exe",
            "Windows installer (ARM64)",
            1,
            False,
            "Experimental",
        ),
        (
            "python-3.14.0b3-embed-amd64.zip",
            "Windows embeddable package (64-bit)",
            1,
            False,
            "",
        ),
        (
            "python-3.14.0b3-embed-arm64.zip",
            "Windows embeddable package (ARM64)",
            1,
            False,
            "",
        ),
        (
            "python-3.14.0b3-embed-win32.zip",
            "Windows embeddable package (32-bit)",
            1,
            False,
            "",
        ),
        (
            "python-3.14.0b3-macos11.pkg",
            "macOS 64-bit universal2 installer",
            2,
            True,
            "for macOS 10.13 and later",
        ),
        ("python-3.14.0b3.exe", "Windows installer (32-bit)", 1, False, ""),
        (
            "windows-3.14.0b3.json",
            "Windows release manifest",
            1,
            False,
            "Install with 'py install 3.14'",
        ),
    ]
