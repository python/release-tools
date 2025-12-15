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
    ["text", "expected"],
    [
        ("3.9.0a0", "390a0"),
        ("3.10.0b3", "3100b3"),
        ("3.11.0rc2", "3110rc2"),
        ("3.12.15", "31215"),
        ("Hello, world!", "Hello-world"),
    ],
)
def test_make_slug(text: str, expected: str) -> None:
    assert add_to_pydotorg.make_slug(text) == expected


def test_build_file_dict(tmp_path: Path) -> None:
    release = "3.14.0rc2"
    release_url = "https://www.python.org/ftp/python/3.14.0"
    release_dir = tmp_path / "3.14.0"
    release_dir.mkdir()

    rfile = "test-artifact.txt"
    (release_dir / rfile).write_text("Hello world")
    (release_dir / f"{rfile}.sigstore").touch()

    assert add_to_pydotorg.build_file_dict(
        str(tmp_path),
        release,
        rfile,
        12,
        "Test artifact",
        34,
        True,
        "Test description",
    ) == {
        "name": "Test artifact",
        "slug": "3140-rc2-Test-artifact",
        "os": "/api/v1/downloads/os/34/",
        "release": "/api/v1/downloads/release/12/",
        "description": "Test description",
        "is_source": False,
        "url": f"{release_url}/test-artifact.txt",
        "md5_sum": "3e25960a79dbc69b674cd4ec67a72c62",
        "sha256sum": "64ec88ca00b268e5ba1a35678a1b5316d212f4f366b2477232534a8aeca37f3c",
        "filesize": 11,
        "download_button": True,
        "sigstore_bundle_file": f"{release_url}/test-artifact.txt.sigstore",
    }


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
        ("3.9.0a0", (3, 9, 0)),
        ("3.10.0b3", (3, 10, 0)),
        ("3.11.0rc2", (3, 11, 0)),
        ("3.12.15", (3, 12, 15)),
    ],
)
def test_base_version_tuple(release: str, expected: tuple[int, int, int]) -> None:
    assert add_to_pydotorg.base_version_tuple(release) == expected


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


@pytest.mark.parametrize(
    ["release", "expected"],
    [
        ((3, 13, 0), "for macOS 10.13 and later"),
        ((3, 14, 0), "for macOS 10.15 and later"),
    ],
)
def test_macos_description(release: tuple[int, int, int], expected: str) -> None:
    assert add_to_pydotorg.macos_description(release) == expected


def test_list_files(fs: FakeFilesystem) -> None:
    # Arrange
    fake_ftp_root = "/fake_ftp_root"
    fs.add_real_file("tests/fake-ftp-files.txt")
    fake_files = Path("tests/fake-ftp-files.txt").read_text().splitlines()
    for fn in fake_files:
        if fn.startswith("#"):  # comment
            continue

        file_path = Path(fake_ftp_root) / "3.14.0" / fn
        if fn.endswith("/"):
            fs.create_dir(file_path)
        else:
            fs.create_file(file_path)

    # Act
    files = list(add_to_pydotorg.list_files(fake_ftp_root, "3.14.0b3"))

    # Assert
    assert files == [
        ("Python-3.14.0b3.tar.xz", "XZ compressed source tarball", "source", True, ""),
        ("Python-3.14.0b3.tgz", "Gzipped source tarball", "source", False, ""),
        (
            "python-3.14.0b3-aarch64-linux-android.tar.gz",
            "Android embeddable package (aarch64)",
            "android",
            False,
            "",
        ),
        (
            "python-3.14.0b3-amd64.exe",
            "Windows installer (64-bit)",
            "windows",
            True,
            "Recommended",
        ),
        (
            "python-3.14.0b3-arm64.exe",
            "Windows installer (ARM64)",
            "windows",
            False,
            "Experimental",
        ),
        (
            "python-3.14.0b3-embed-amd64.zip",
            "Windows embeddable package (64-bit)",
            "windows",
            False,
            "",
        ),
        (
            "python-3.14.0b3-embed-arm64.zip",
            "Windows embeddable package (ARM64)",
            "windows",
            False,
            "",
        ),
        (
            "python-3.14.0b3-embed-win32.zip",
            "Windows embeddable package (32-bit)",
            "windows",
            False,
            "",
        ),
        (
            "python-3.14.0b3-macos11.pkg",
            "macOS installer",
            "macos",
            True,
            "for macOS 10.15 and later",
        ),
        (
            "python-3.14.0b3-x86_64-linux-android.tar.gz",
            "Android embeddable package (x86_64)",
            "android",
            False,
            "",
        ),
        ("python-3.14.0b3.exe", "Windows installer (32-bit)", "windows", False, ""),
        (
            "windows-3.14.0b3.json",
            "Windows release manifest",
            "windows",
            False,
            "Install with 'py install 3.14'",
        ),
    ]
