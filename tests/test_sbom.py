import pytest
import random
import hashlib
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
        "a": [1, 2, 3, {"b": [4, "c", [7, True, "2", {}]]}]
    }
    sbom.normalize_sbom_data(data)
    assert data == {'a': [1, 2, 3, {'b': ['c', 4, ['2', 7, True, {}]]}]}
