import pathlib
import json
import random
import hashlib
import re
import unittest.mock

import pytest

import sbom


@pytest.mark.parametrize(
    ["package_sha1s", "package_verification_code"],
    [
        # No files -> empty SHA1
        ([], hashlib.sha1().hexdigest()),
        # One file -> SHA1(SHA1(file))
        (["F" * 40], hashlib.sha1(b"f" * 40).hexdigest()),
        # Tests ordering and lowercasing of SHA1s
        (["0" * 40, "e" * 40, "F" * 40], hashlib.sha1((b"0" * 40) + (b"e" * 40) + (b"f" * 40)).hexdigest())
    ]
)
def test_calculate_package_verification_code(package_sha1s, package_verification_code):
    # Randomize because PackageVerificationCode is deterministic.
    random.shuffle(package_sha1s)

    input_sbom = {
        "files": [
            {
                "SPDXID": f"SPDXRef-FILE-{package_sha1}",
                "checksums": [{"algorithm": "SHA1", "checksumValue": package_sha1}]
            } for package_sha1 in package_sha1s
        ],
        "packages": [{"SPDXID": "SPDXRef-PACKAGE", "filesAnalyzed": True}],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-PACKAGE",
                "relatedSpdxElement": f"SPDXRef-FILE-{package_sha1}",
                "relationshipType": "CONTAINS"
            }
            for package_sha1 in package_sha1s
        ]
    }

    sbom.calculate_package_verification_codes(input_sbom)

    assert input_sbom["packages"][0]["packageVerificationCode"] == {
        "packageVerificationCodeValue": package_verification_code
    }


def test_normalization():
    # Test that arbitrary JSON data can be normalized.
    # Normalization doesn't have to make too much sense,
    # only needs to be reproducible.
    data = {
        "a": [1, 2, 3, {"b": [4, "c", [7, True, "2", {}]]}],
        # This line tests that inner structures are sorted first.
        "b": [[1, 2, "b"], [2, 1, "a"]]
    }
    sbom.normalize_sbom_data(data)
    assert data == {
        "a": [1, 2, 3, {"b": ["c", 4, ["2", 7, True, {}]]}],
        "b": [["a", 1, 2], ["b", 1, 2]]
    }


def test_fetch_project_metadata_from_pypi(mocker):
    mock_urlopen = mocker.patch("sbom.urlopen")
    mock_urlopen.return_value = unittest.mock.Mock()

    # This is only a partial response using the information
    # that this function uses.
    mock_urlopen.return_value.read.return_value = json.dumps({
        "urls": [
            {
                "digests": {
                    "blake2b_256": "94596638090c25e9bc4ce0c42817b5a234e183872a1129735a9330c472cc2056",
                    "md5": "1331aabb4d1a2677f493effeebda3605",
                    "sha256": "ea9bd1a847e8c5774a5777bb398c19e80bcd4e2aa16a4b301b718fe6f593aba2"
                },
                "filename": "pip-24.0.tar.gz",
                "packagetype": "sdist",
                "url": "https://files.pythonhosted.org/packages/.../pip-24.0.tar.gz",
            },
            {
                "digests": {
                    "blake2b_256": "8a6a19e9fe04fca059ccf770861c7d5721ab4c2aebc539889e97c7977528a53b",
                    "md5": "74e3c5e4082113b1239ca0e9abfd1e82",
                    "sha256": "ba0d021a166865d2265246961bec0152ff124de910c5cc39f1156ce3fa7c69dc"
                },
                "filename": "pip-24.0-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "url": "https://files.pythonhosted.org/packages/.../pip-24.0-py3-none-any.whl",
            }
        ]
    }).encode()

    # Default filename is the wheel
    download_url, checksum_sha256 = sbom.fetch_package_metadata_from_pypi(
        project="pip",
        version="24.0",
    )

    mock_urlopen.assert_called_once_with("https://pypi.org/pypi/pip/24.0/json")
    assert download_url == "https://files.pythonhosted.org/packages/.../pip-24.0-py3-none-any.whl"
    assert checksum_sha256 == "ba0d021a166865d2265246961bec0152ff124de910c5cc39f1156ce3fa7c69dc"

    # If we ask for the sdist (which we don't do normally)
    # then it'll be returned instead.
    download_url, checksum_sha256 = sbom.fetch_package_metadata_from_pypi(
        project="pip",
        version="24.0",
        filename="pip-24.0.tar.gz"
    )

    assert download_url == "https://files.pythonhosted.org/packages/.../pip-24.0.tar.gz"
    assert checksum_sha256 == "ea9bd1a847e8c5774a5777bb398c19e80bcd4e2aa16a4b301b718fe6f593aba2"


def test_create_cpython_sbom():
    sbom_data = {"packages": []}

    artifact_path = str(pathlib.Path(__file__).parent / "fake-artifact.txt")
    sbom.create_cpython_sbom(
        sbom_data, cpython_version="3.13.0", artifact_path=artifact_path
    )

    assert re.fullmatch(
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
        sbom_data["creationInfo"].pop("created")
    )
    assert re.fullmatch(
        r"^Tool: ReleaseTools-[a-f0-9]+$",
        sbom_data["creationInfo"]["creators"].pop(1)
    )

    assert sbom_data == {
        "packages": [
            {
                "SPDXID": "SPDXRef-PACKAGE-cpython",
                "name": "CPython",
                "versionInfo": "3.13.0",
                "licenseConcluded": "PSF-2.0",
                "originator": "Organization: Python Software Foundation",
                "supplier": "Organization: Python Software Foundation",
                "packageFileName": "fake-artifact.txt",
                "externalRefs": [
                    {
                        "referenceCategory": "SECURITY",
                        "referenceLocator": "cpe:2.3:a:python:python:3.13.0:*:*:*:*:*:*:*",
                        "referenceType": "cpe23Type",
                    }
                ],
                "primaryPackagePurpose": "SOURCE",
                "downloadLocation": "https://www.python.org/ftp/python/3.13.0/fake-artifact.txt",
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    }
                ],
            }
        ],
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "name": "CPython SBOM",
        "dataLicense": "CC0-1.0",
        "documentNamespace": "https://www.python.org/ftp/python/3.13.0/fake-artifact.txt.spdx.json",
        "creationInfo": {
            "creators": [
                "Person: Python Release Managers",
            ],
            "licenseListVersion": "3.22",
        },
    }


@pytest.mark.parametrize(
    ["cpython_version", "download_location"],
    [
        ("3.13.0", "https://www.python.org/ftp/python/3.13.0/fake-artifact.txt"),
        ("3.11.0a1", "https://www.python.org/ftp/python/3.11.0/fake-artifact.txt"),
        ("3.12.0b2", "https://www.python.org/ftp/python/3.12.0/fake-artifact.txt"),
        ("3.13.0rc3", "https://www.python.org/ftp/python/3.13.0/fake-artifact.txt"),
    ]
)
def test_create_cpython_sbom_pre_release_download_location(cpython_version, download_location):
    sbom_data = {"packages": []}

    artifact_path = str(pathlib.Path(__file__).parent / "fake-artifact.txt")
    sbom.create_cpython_sbom(
        sbom_data, cpython_version=cpython_version, artifact_path=artifact_path
    )

    assert sbom_data["packages"][0]["downloadLocation"] == download_location
