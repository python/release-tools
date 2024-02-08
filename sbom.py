"""
Utility which creates Software Bill-of-Materials (SBOM)
for CPython release artifacts. Can also be run manually with:

    $ python sbom.py <artifact>

For example:

    $ python sbom.py ./Python-3.13.0a3.tar.xz

"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile


def spdx_id(value: str) -> str:
    """Encode a value into characters that are valid in an SPDX ID"""
    return re.sub(r"[^a-zA-Z0-9.\-]+", "-", value)


def calculate_package_verification_codes(sbom) -> None:
    """
    Calculate SPDX 'packageVerificationCode' values for
    each package with 'filesAnalyzed' set to 'true'.
    Mutates the values within the passed structure.

    The code is SHA1 of a concatenated and sorted list of file SHA1s.
    """

    # Find all packages which we need to calculate package verification codes for.
    sbom_file_id_to_package_id = {}
    sbom_package_id_to_file_sha1s: dict[str, list[bytes]] = {}
    for sbom_package in sbom["packages"]:
        # If this value is 'false' we skip calculating.
        if sbom_package["filesAnalyzed"]:
            sbom_package_id = sbom_package["SPDXID"]
            sbom_package_id_to_file_sha1s[sbom_package_id] = []

    # Next pass we do is over relationships,
    # we need to find all files that belong to each package.
    for sbom_relationship in sbom["relationships"]:
        sbom_relationship_type = sbom_relationship["relationshipType"]
        sbom_element_id = sbom_relationship["spdxElementId"]
        sbom_related_element_id = sbom_relationship["relatedSpdxElement"]

        # We're looking for '<package> CONTAINS <file>' relationships
        if (
            sbom_relationship_type != "CONTAINS"
            or sbom_element_id not in sbom_package_id_to_file_sha1s
            or not sbom_related_element_id.startswith("SPDXRef-FILE-")
        ):
            continue

        # Found one! Add it to our mapping.
        sbom_file_id_to_package_id[sbom_related_element_id] = sbom_element_id

    # Now we do a single pass on files, appending all SHA1 values along the way.
    for sbom_file in sbom["files"]:
        # Attempt to match this file to a package.
        sbom_file_id = sbom_file["SPDXID"]
        if sbom_file_id not in sbom_file_id_to_package_id:
            continue
        sbom_package_id = sbom_file_id_to_package_id[sbom_file_id]

        # Find the SHA1 checksum for the file.
        for sbom_file_checksum in sbom_file["checksums"]:
            if sbom_file_checksum["algorithm"] == "SHA1":
                # We lowercase the value as that's what's required by the algorithm.
                sbom_file_checksum_sha1 = (
                    sbom_file_checksum["checksumValue"].lower().encode("ascii")
                )
                break
        else:
            raise ValueError(f"Can't find SHA1 checksum for '{sbom_file_id}'")

        sbom_package_id_to_file_sha1s[sbom_package_id].append(sbom_file_checksum_sha1)

    # Finally we iterate over the packages again and calculate the final package verification code values.
    for sbom_package in sbom["packages"]:
        sbom_package_id = sbom_package["SPDXID"]
        if sbom_package_id not in sbom_package_id_to_file_sha1s:
            continue

        # Package verification code is the SHA1 of ASCII values ascending-sorted.
        sbom_package_verification_code = hashlib.sha1(
            b"".join(sorted(sbom_package_id_to_file_sha1s[sbom_package_id]))
        ).hexdigest()

        sbom_package["packageVerificationCode"] = {
            "packageVerificationCodeValue": sbom_package_verification_code
        }


def get_release_tools_commit_sha() -> str:
    """Gets the git commit SHA of the release-tools repository"""
    git_prefix = os.path.abspath(os.path.dirname(__file__))
    stdout = subprocess.check_output(
        ["git", "rev-parse", "--prefix", git_prefix, "HEAD"],
        cwd=git_prefix
    ).decode("ascii")
    assert re.match(r"^[a-f0-9]{40,}$", stdout)
    return stdout


def normalize_sbom_data(sbom_data):
    """
    Normalize SBOM data in-place by recursion
    and sorting lists by some repeatable key.
    """
    def recursive_sort_in_place(value):
        if isinstance(value, list):
            # We need to recurse first so bottom-most elements are sorted first.
            for item in value:
                recursive_sort_in_place(item)

            # Otherwise this key might change depending on the unsorted order of items.
            value.sort(key=lambda item: json.dumps(item, sort_keys=True))

        # Dictionaries are the only other containers and keys
        # are already handled by json.dumps(sort_keys=True).
        elif isinstance(value, dict):
            for dict_val in value.values():
                recursive_sort_in_place(dict_val)

    recursive_sort_in_place(sbom_data)


def create_sbom_for_source_tarball(tarball_path: str):
    """Stitches together an SBOM for a source tarball"""
    tarball_name = os.path.basename(tarball_path)

    # Open the tarball with known compression settings.
    if tarball_name.endswith(".tgz"):
        tarball = tarfile.open(tarball_path, mode="r:gz")
    elif tarball_name.endswith(".tar.xz"):
        tarball = tarfile.open(tarball_path, mode="r:xz")
    else:
        raise ValueError(f"Unknown tarball format: '{tarball_name}'")

    # Parse the CPython version from the tarball.
    # Calculate the download locations from the CPython version and tarball name.
    cpython_version = re.match(r"^Python-([0-9abrc.]+)\.t", tarball_name).group(1)
    cpython_version_without_suffix = re.match(r"^([0-9.]+)", cpython_version).group(1)
    tarball_download_location = f"https://www.python.org/ftp/python/{cpython_version_without_suffix}/{tarball_name}"

    # Take a hash of the tarball
    with open(tarball_path, mode="rb") as f:
        tarball_checksum_sha256 = hashlib.sha256(f.read()).hexdigest()

    # There should be an SBOM included in the tarball.
    # If there's not we can't create an SBOM.
    try:
        sbom_tarball_member = tarball.getmember(f"Python-{cpython_version}/Misc/sbom.spdx.json")
    except KeyError:
        raise ValueError(
            "Tarball doesn't contain an SBOM at 'Misc/sbom.spdx.json'"
        ) from None
    sbom_bytes = tarball.extractfile(sbom_tarball_member).read()

    sbom = json.loads(sbom_bytes)
    sbom.update({
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "name": "CPython SBOM",
        "dataLicense": "CC0-1.0",
        # Naming done according to OpenSSF SBOM WG recommendations.
        # See: https://github.com/ossf/sbom-everywhere/blob/main/reference/sbom_naming.md
        "documentNamespace": f"{tarball_download_location}.spdx.json",
        "creationInfo": {
            "created": (
                datetime.datetime.now(tz=datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
            "creators": [
                "Person: Python Release Managers",
                f"Tool: ReleaseTools-{get_release_tools_commit_sha()}",
            ],
            # Version of the SPDX License ID list.
            # This shouldn't need to be updated often, if ever.
            "licenseListVersion": "3.22",
        },
    })

    # Create the SBOM entry for the CPython package. We use
    # the SPDXID later on for creating relationships to files.
    sbom_cpython_package = {
        "SPDXID": "SPDXRef-PACKAGE-cpython",
        "name": "CPython",
        "versionInfo": cpython_version,
        "licenseConcluded": "PSF-2.0",
        "originator": "Organization: Python Software Foundation",
        "supplier": "Organization: Python Software Foundation",
        "packageFileName": tarball_name,
        "externalRefs": [
            {
                "referenceCategory": "SECURITY",
                "referenceLocator": f"cpe:2.3:a:python:python:{cpython_version}:*:*:*:*:*:*:*",
                "referenceType": "cpe23Type",
            }
        ],
        "primaryPackagePurpose": "SOURCE",
        "downloadLocation": tarball_download_location,
        "checksums": [{"algorithm": "SHA256", "checksumValue": tarball_checksum_sha256}],
    }

    # The top-level CPython package depends on every vendored sub-package.
    for sbom_package in sbom["packages"]:
        sbom["relationships"].append({
            "spdxElementId": sbom_cpython_package["SPDXID"],
            "relatedSpdxElement": sbom_package["SPDXID"],
            "relationshipType": "DEPENDS_ON",
        })

    sbom["packages"].append(sbom_cpython_package)

    # Extract all currently known files from the SBOM with their checksums.
    known_sbom_files = {}
    for sbom_file in sbom["files"]:
        sbom_filename = sbom_file["fileName"]

        # Look for the expected SHA256 checksum.
        for sbom_file_checksum in sbom_file["checksums"]:
            if sbom_file_checksum["algorithm"] == "SHA256":
                known_sbom_files[sbom_filename] = (
                    sbom_file_checksum["checksumValue"]
                )
                break
        else:
            raise ValueError(
                f"Couldn't find expected SHA256 checksum in SBOM for file '{sbom_filename}'"
            )

    # Now we walk the tarball and compare known files to our expected checksums in the SBOM.
    # All files that aren't already in the SBOM can be added as "CPython" files.
    for member in tarball.getmembers():
        if member.isdir():  # Skip directories!
            continue

        # Get the member from the tarball. CPython prefixes all of its
        # source code with 'Python-{version}/...'.
        assert member.isfile() and member.name.startswith(f"Python-{cpython_version}/")

        # Calculate the hashes, either for comparison with a known value
        # or to embed in the SBOM as a new file. SHA1 is only used because
        # SPDX requires it for all file entries.
        file_bytes = tarball.extractfile(member).read()
        actual_file_checksum_sha1 = hashlib.sha1(file_bytes).hexdigest()
        actual_file_checksum_sha256 = hashlib.sha256(file_bytes).hexdigest()

        # Remove the 'Python-{version}/...' prefix for the SPDXID and fileName.
        member_name_no_prefix = member.name.split('/', 1)[1]

        # We've already seen this file, so we check it hasn't been modified and continue on.
        if member_name_no_prefix in known_sbom_files:
            # If there's a hash mismatch we raise an error, something isn't right!
            expected_file_checksum_sha256 = known_sbom_files.pop(member_name_no_prefix)
            if expected_file_checksum_sha256 != actual_file_checksum_sha256:
                raise ValueError(f"Mismatched checksum for file '{member_name_no_prefix}'")

        # If this is a new file, then it's a part of the 'CPython' SBOM package.
        else:
            sbom_file_spdx_id = spdx_id(f"SPDXRef-FILE-{member_name_no_prefix}")
            sbom["files"].append(
                {
                    "SPDXID": sbom_file_spdx_id,
                    "fileName": member_name_no_prefix,
                    "checksums": [
                        {
                            "algorithm": "SHA1",
                            "checksumValue": actual_file_checksum_sha1,
                        },
                        {
                            "algorithm": "SHA256",
                            "checksumValue": actual_file_checksum_sha256,
                        },
                    ],
                }
            )
            sbom["relationships"].append(
                {
                    "spdxElementId": sbom_cpython_package["SPDXID"],
                    "relatedSpdxElement": sbom_file_spdx_id,
                    "relationshipType": "CONTAINS",
                }
            )

    # If there are any known files that weren't found in the
    # source tarball we want to raise an error.
    if known_sbom_files:
        raise ValueError(
            f"Some files from source SBOM aren't accounted for "
            f"in source tarball: {sorted(known_sbom_files)!r}"
        )

    # Final relationship, this SBOM describes the CPython package.
    sbom["relationships"].append(
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relatedSpdxElement": sbom_cpython_package["SPDXID"],
            "relationshipType": "DESCRIBES",
        }
    )

    # Apply the 'supplier' tag to every package since we're shipping
    # the package in the tarball itself. Originator field is used for maintainers.
    for sbom_package in sbom["packages"]:
        sbom_package["supplier"] = "Organization: Python Software Foundation"
        sbom_package["filesAnalyzed"] = True

    # Calculate the 'packageVerificationCode' values for files in packages.
    calculate_package_verification_codes(sbom)

    # Normalize SBOM structures for reproducibility.
    normalize_sbom_data(sbom)

    return sbom


def main() -> None:
    tarball_path = sys.argv[1]
    sbom_data = create_sbom_for_source_tarball(tarball_path)
    print(json.dumps(sbom_data, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
