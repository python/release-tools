"""
Utility which creates Software Bill-of-Materials (SBOM)
for CPython release artifacts. Can also be run manually with:

    $ python sbom.py <artifact>

For example:

    $ python sbom.py ./Python-3.13.0a3.tar.xz

"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import typing
import zipfile
from functools import cache
from pathlib import Path
from typing import Any, LiteralString, NotRequired, TypedDict, cast
from urllib.request import urlopen


class SBOM(TypedDict):
    SPDXID: str
    spdxVersion: str
    name: str
    dataLicense: str
    documentNamespace: str
    creationInfo: CreationInfo
    packages: list[Package]
    files: list[File]
    relationships: list[Relationship]


class Package(TypedDict):
    SPDXID: str
    name: str
    versionInfo: str
    packageFileName: NotRequired[str]
    supplier: NotRequired[str]
    originator: NotRequired[str]
    licenseConcluded: str
    downloadLocation: str
    checksums: list[Checksum]
    primaryPackagePurpose: str
    packageVerificationCode: NotRequired[PackageVerificationCode]
    externalRefs: list[Ref]
    filesAnalyzed: NotRequired[bool]


class File(TypedDict):
    SPDXID: str
    fileName: str
    checksums: list[Checksum]


class Relationship(TypedDict):
    spdxElementId: str
    relatedSpdxElement: str
    relationshipType: str


class Checksum(TypedDict):
    algorithm: str
    checksumValue: str


class PackageVerificationCode(TypedDict):
    packageVerificationCodeValue: str


class Ref(TypedDict):
    referenceCategory: str
    referenceLocator: str
    referenceType: str


class CreationInfo(TypedDict):
    created: str  # timestamp
    creators: list[str]
    licenseListVersion: str


# Cache of values that we've seen already. We use this
# to de-duplicate values and their corresponding SPDX ID.
_SPDX_IDS_TO_VALUES: dict[str, Any] = {}


@cache
def spdx_id(value: LiteralString) -> str:
    """Encode a value into characters that are valid in an SPDX ID"""
    value_as_spdx_id = re.sub(r"[^a-zA-Z0-9.\-]+", "-", value)

    # The happy path is there are no collisions.
    # But collisions can happen, especially in file paths.
    # We append a hash suffix in those cases.
    if _SPDX_IDS_TO_VALUES.setdefault(value_as_spdx_id, value) != value:
        suffix = hashlib.sha256(value.encode()).hexdigest()[:8]
        value_as_spdx_id = f"{value_as_spdx_id}-{suffix}"
        assert _SPDX_IDS_TO_VALUES.setdefault(value_as_spdx_id, value) == value

    return value_as_spdx_id


def calculate_package_verification_codes(sbom: SBOM) -> None:
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
        if sbom_package.get("filesAnalyzed", False):
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
    stdout = (
        subprocess.check_output(
            ["git", "rev-parse", "--prefix", git_prefix, "HEAD"], cwd=git_prefix
        )
        .decode("ascii")
        .strip()
    )
    assert re.fullmatch(r"^[a-f0-9]{40,}$", stdout)
    return stdout


def normalize_sbom_data(sbom_data: SBOM) -> None:
    """
    Normalize SBOM data in-place by recursion
    and sorting lists by some repeatable key.
    """

    def recursive_sort_in_place(value: list[Any] | dict[str, Any]) -> None:
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

    recursive_sort_in_place(cast(dict[str, Any], sbom_data))


def check_sbom_data(sbom_data):
    """Check SBOM data for common issues"""

    def check_id_duplicates(sbom_components: list[dict[str, typing.Any]]) -> set[str]:
        all_ids = set()
        for sbom_component in sbom_components:
            sbom_component_id = sbom_component["SPDXID"]
            assert sbom_component_id not in all_ids
            all_ids.add(sbom_component_id)
        return all_ids

    all_package_ids = check_id_duplicates(sbom_data["packages"])
    all_file_ids = check_id_duplicates(sbom_data["files"])

    # Check that no files and packages have the same ID.
    assert not all_package_ids.intersection(all_file_ids)
    all_sbom_ids = all_package_ids | all_file_ids

    # Check that all relationships use existing IDs.
    for sbom_relationship in sbom_data["relationships"]:

        # The exception being 'DESCRIBES' with the meta 'document' ID
        if (
            sbom_relationship["spdxElementId"] == "SPDXRef-DOCUMENT"
            and sbom_relationship["relationshipType"] == "DESCRIBES"
        ):
            continue

        assert sbom_relationship["spdxElementId"] in all_sbom_ids
        assert sbom_relationship["relatedSpdxElement"] in all_sbom_ids


def fetch_package_metadata_from_pypi(
    project: str, version: str, filename: str | None = None
) -> tuple[str, str]:
    """
    Fetches the SHA256 checksum and download location from PyPI.
    If we're given a filename then we match with that, otherwise we use wheels.
    """
    # Get the package download URL from PyPI.
    try:
        raw_text = urlopen(f"https://pypi.org/pypi/{project}/{version}/json").read()
        release_metadata = json.loads(raw_text)
        url: dict[str, typing.Any]

        # Look for a matching artifact filename and then check
        # its remote checksum to the local one.
        for url in release_metadata["urls"]:
            # pip can only use Python-only dependencies, so there's
            # no risk of picking the 'incorrect' wheel here.
            if (filename is None and url["packagetype"] == "bdist_wheel") or (
                filename is not None and url["filename"] == filename
            ):
                break
        else:
            raise ValueError(f"No matching filename on PyPI for '{filename}'")

        # Successfully found the download URL for the matching artifact.
        download_url = url["url"]
        checksum_sha256 = url["digests"]["sha256"]
        return download_url, checksum_sha256

    except Exception as e:
        raise ValueError(
            f"Couldn't fetch metadata for project '{project}' from PyPI: {e}"
        )


def remove_pip_from_sbom(sbom_data: SBOM) -> None:
    """
    Removes pip and its dependencies from the SBOM data.
    This is only necessary if there's potential we get
    pip SBOM data from the CPython source SBOM.
    """
    sbom_pip_spdx_id = spdx_id("SPDXRef-PACKAGE-pip")
    sbom_spdx_ids_to_remove = {sbom_pip_spdx_id}

    # Find all package SPDXIDs that pip depends on.
    for sbom_relationship in sbom_data["relationships"]:
        if (
            sbom_relationship["relationshipType"] == "DEPENDS_ON"
            and sbom_relationship["spdxElementId"] == sbom_pip_spdx_id
        ):
            sbom_spdx_ids_to_remove.add(sbom_relationship["relatedSpdxElement"])

    # Remove all the packages and relationships.
    sbom_data["packages"] = [
        sbom_package
        for sbom_package in sbom_data["packages"]
        if sbom_package["SPDXID"] not in sbom_spdx_ids_to_remove
    ]
    sbom_data["relationships"] = [
        sbom_relationship
        for sbom_relationship in sbom_data["relationships"]
        if sbom_relationship["relatedSpdxElement"] not in sbom_spdx_ids_to_remove
    ]


def create_pip_sbom_from_wheel(
    sbom_data: SBOM, pip_wheel_filename: str, pip_wheel_bytes: bytes
) -> None:
    """
    pip is a part of a packaging ecosystem (Python, surprise!) so it's actually
    automatable to discover the metadata we need like the version and checksums
    so let's do that on behalf of our friends at the PyPA. This function also
    discovers vendored packages within pip and fetches their metadata.
    """
    # Remove pip from the SBOM in case it's included in the CPython source code SBOM.
    remove_pip_from_sbom(sbom_data)

    # Wheel filename format puts the version right after the project name.
    pip_version = pip_wheel_filename.split("-")[1]
    pip_checksum_sha256 = hashlib.sha256(pip_wheel_bytes).hexdigest()

    pip_download_url, pip_actual_sha256 = fetch_package_metadata_from_pypi(
        project="pip",
        version=pip_version,
        filename=pip_wheel_filename,
    )
    if pip_actual_sha256 != pip_checksum_sha256:
        raise ValueError("pip wheel checksum doesn't match PyPI")

    # Parse 'pip/_vendor/vendor.txt' from the wheel for sub-dependencies.
    with zipfile.ZipFile(io.BytesIO(pip_wheel_bytes)) as whl:
        vendor_txt_data = whl.read("pip/_vendor/vendor.txt").decode()

        # With this version regex we're assuming that pip isn't using pre-releases.
        # If any version doesn't match we get a failure below, so we're safe doing this.
        version_pin_re = re.compile(r"^([a-zA-Z0-9_.-]+)==([0-9.]*[0-9])$")
        sbom_pip_dependency_spdx_ids = set()
        for line in vendor_txt_data.splitlines():
            line = line.partition("#")[0].strip()  # Strip comments and whitespace.
            if not line:  # Skip empty lines.
                continue

            # Non-empty lines we must be able to match.
            match = version_pin_re.match(line)
            assert (
                match is not None
            ), f"Unparseable line in vendor.txt: {line!r}"  # Make mypy happy.

            # Parse out and normalize the project name.
            project_name, project_version = match.groups()
            project_name = project_name.lower()

            # Fetch the metadata from PyPI
            project_download_url, project_checksum_sha256 = (
                fetch_package_metadata_from_pypi(project_name, project_version)
            )

            # Update our SBOM data with what we received from PyPI.
            sbom_project_spdx_id = spdx_id(f"SPDXRef-PACKAGE-{project_name}")
            sbom_pip_dependency_spdx_ids.add(sbom_project_spdx_id)
            sbom_data["packages"].append(
                {
                    "SPDXID": sbom_project_spdx_id,
                    "name": project_name,
                    "versionInfo": project_version,
                    "downloadLocation": project_download_url,
                    "checksums": [
                        {
                            "algorithm": "SHA256",
                            "checksumValue": project_checksum_sha256,
                        }
                    ],
                    "externalRefs": [
                        {
                            "referenceCategory": "PACKAGE_MANAGER",
                            "referenceLocator": f"pkg:pypi/{project_name}@{project_version}",
                            "referenceType": "purl",
                        },
                    ],
                    "primaryPackagePurpose": "SOURCE",
                    "licenseConcluded": "NOASSERTION",
                }
            )

    # Now we add pip to the SBOM and dependency relationships
    sbom_pip_spdx_id = spdx_id("SPDXRef-PACKAGE-pip")
    sbom_data["packages"].append(
        {
            "SPDXID": sbom_pip_spdx_id,
            "name": "pip",
            "versionInfo": pip_version,
            "originator": "Organization: Python Packaging Authority",
            "licenseConcluded": "NOASSERTION",
            "downloadLocation": pip_download_url,
            "checksums": [
                {"algorithm": "SHA256", "checksumValue": pip_checksum_sha256}
            ],
            "externalRefs": [
                {
                    "referenceCategory": "SECURITY",
                    "referenceLocator": f"cpe:2.3:a:pypa:pip:{pip_version}:*:*:*:*:*:*:*",
                    "referenceType": "cpe23Type",
                },
                {
                    "referenceCategory": "PACKAGE_MANAGER",
                    "referenceLocator": f"pkg:pypi/pip@{pip_version}",
                    "referenceType": "purl",
                },
            ],
            "primaryPackagePurpose": "SOURCE",
        }
    )
    for sbom_dep_spdx_id in sorted(sbom_pip_dependency_spdx_ids):
        sbom_data["relationships"].append(
            {
                "spdxElementId": sbom_pip_spdx_id,
                "relatedSpdxElement": sbom_dep_spdx_id,
                "relationshipType": "DEPENDS_ON",
            }
        )

    # Finally, CPython depends on pip.
    sbom_data["relationships"].append(
        {
            "spdxElementId": "SPDXRef-PACKAGE-cpython",
            "relatedSpdxElement": sbom_pip_spdx_id,
            "relationshipType": "DEPENDS_ON",
        }
    )


def create_cpython_sbom(
    sbom_data: SBOM,
    cpython_version: str,
    artifact_path: str,
) -> None:
    """Creates the top-level SBOM metadata and the CPython SBOM package."""

    if m := re.match(pat := r"^([0-9.]+)", cpython_version):
        cpython_version_without_suffix = m.group(1)
    else:
        raise ValueError(f"Invalid {cpython_version=}, expected {pat!r}")
    artifact_name = os.path.basename(artifact_path)
    artifact_download_location = f"https://www.python.org/ftp/python/{cpython_version_without_suffix}/{artifact_name}"

    # Take a hash of the artifact
    with open(artifact_path, mode="rb") as f:
        artifact_checksum_sha256 = hashlib.sha256(f.read()).hexdigest()

    sbom_data.update(
        {
            "SPDXID": "SPDXRef-DOCUMENT",
            "spdxVersion": "SPDX-2.3",
            "name": "CPython SBOM",
            "dataLicense": "CC0-1.0",
            # Naming done according to OpenSSF SBOM WG recommendations.
            # See: https://github.com/ossf/sbom-everywhere/blob/main/reference/sbom_naming.md
            "documentNamespace": f"{artifact_download_location}.spdx.json",
            "creationInfo": {
                "created": (
                    datetime.datetime.now(tz=datetime.timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                ),
                "creators": [
                    "Person: Python Release Managers",
                    f"Tool: ReleaseTools-{get_release_tools_commit_sha()}",
                ],
                # Version of the SPDX License ID list.
                # This shouldn't need to be updated often, if ever.
                "licenseListVersion": "3.22",
            },
        }
    )

    # Create the SBOM entry for the CPython package. We use
    # the SPDXID later on for creating relationships to files.
    sbom_cpython_package: Package = {
        "SPDXID": "SPDXRef-PACKAGE-cpython",
        "name": "CPython",
        "versionInfo": cpython_version,
        "licenseConcluded": "PSF-2.0",
        "originator": "Organization: Python Software Foundation",
        "supplier": "Organization: Python Software Foundation",
        "packageFileName": artifact_name,
        "externalRefs": [
            {
                "referenceCategory": "SECURITY",
                "referenceLocator": f"cpe:2.3:a:python:python:{cpython_version}:*:*:*:*:*:*:*",
                "referenceType": "cpe23Type",
            }
        ],
        "primaryPackagePurpose": "SOURCE",
        "downloadLocation": artifact_download_location,
        "checksums": [
            {"algorithm": "SHA256", "checksumValue": artifact_checksum_sha256}
        ],
    }

    # The top-level CPython package depends on every vendored sub-package.
    for sbom_package in sbom_data["packages"]:
        sbom_data["relationships"].append(
            {
                "spdxElementId": sbom_cpython_package["SPDXID"],
                "relatedSpdxElement": sbom_package["SPDXID"],
                "relationshipType": "DEPENDS_ON",
            }
        )

    sbom_data["packages"].append(sbom_cpython_package)


def create_sbom_for_source_tarball(tarball_path: str) -> SBOM:
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

    if m := re.match(pat := r"^Python-([0-9abrc.]+)\.t", tarball_name):
        cpython_version = m.group(1)
    else:
        raise ValueError(f"Invalid {tarball_name=}, expected {pat!r}")

    # There should be an SBOM included in the tarball.
    # If there's not we can't create an SBOM.
    try:
        sbom_tarball_member = tarball.getmember(
            f"Python-{cpython_version}/Misc/sbom.spdx.json"
        )
    except KeyError:
        raise ValueError(
            "Tarball doesn't contain an SBOM at 'Misc/sbom.spdx.json'"
        ) from None
    reader = tarball.extractfile(sbom_tarball_member)
    assert reader, f"{sbom_tarball_member} is not a file in {tarball_path}"
    sbom_bytes = reader.read()
    sbom_data: SBOM = json.loads(sbom_bytes)

    create_cpython_sbom(
        sbom_data, cpython_version=cpython_version, artifact_path=tarball_path
    )
    sbom_cpython_package_spdx_id = spdx_id("SPDXRef-PACKAGE-cpython")

    # Find the pip wheel in ensurepip in the tarball
    for member in tarball.getmembers():
        if match := re.match(
            rf"^Python-{cpython_version}/Lib/ensurepip/_bundled/(pip-.*\.whl)$",
            member.name,
        ):
            pip_wheel_filename = match.group(1)
            reader = tarball.extractfile(member)
            assert reader, f"{member} is not a file in {tarball_path}"
            pip_wheel_bytes = reader.read()
            break
    else:
        raise ValueError("Could not find pip wheel in 'Lib/ensurepip/_bundled/...'")

    # Now add pip to the SBOM. We do this after the above step to avoid
    # CPython being dependent on packages that pip is dependent on.
    create_pip_sbom_from_wheel(
        sbom_data=sbom_data,
        pip_wheel_filename=pip_wheel_filename,
        pip_wheel_bytes=pip_wheel_bytes,
    )

    # Extract all currently known files from the SBOM with their checksums.
    known_sbom_files = {}
    for sbom_file in sbom_data["files"]:
        sbom_filename = sbom_file["fileName"]

        # Look for the expected SHA256 checksum.
        for sbom_file_checksum in sbom_file["checksums"]:
            if sbom_file_checksum["algorithm"] == "SHA256":
                known_sbom_files[sbom_filename] = sbom_file_checksum["checksumValue"]
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
        reader = tarball.extractfile(member)
        assert reader, f"{member} is not a file in {tarball_path}"
        file_bytes = reader.read()
        actual_file_checksum_sha1 = hashlib.sha1(file_bytes).hexdigest()
        actual_file_checksum_sha256 = hashlib.sha256(file_bytes).hexdigest()

        # Remove the 'Python-{version}/...' prefix for the SPDXID and fileName.
        member_name_no_prefix = member.name.split("/", 1)[1]

        # We've already seen this file, so we check it hasn't been modified and continue on.
        if member_name_no_prefix in known_sbom_files:
            # If there's a hash mismatch we raise an error, something isn't right!
            expected_file_checksum_sha256 = known_sbom_files.pop(member_name_no_prefix)
            if expected_file_checksum_sha256 != actual_file_checksum_sha256:
                raise ValueError(
                    f"Mismatched checksum for file '{member_name_no_prefix}'"
                )

        # If this is a new file, then it's a part of the 'CPython' SBOM package.
        else:
            sbom_file_spdx_id = spdx_id(f"SPDXRef-FILE-{member_name_no_prefix}")
            sbom_data["files"].append(
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
            sbom_data["relationships"].append(
                {
                    "spdxElementId": sbom_cpython_package_spdx_id,
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
    sbom_data["relationships"].append(
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relatedSpdxElement": sbom_cpython_package_spdx_id,
            "relationshipType": "DESCRIBES",
        }
    )

    # Apply the 'supplier' tag to every package since we're shipping
    # the package in the tarball itself. Originator field is used for maintainers.
    for sbom_package in sbom_data["packages"]:
        sbom_package["supplier"] = "Organization: Python Software Foundation"
        sbom_package["filesAnalyzed"] = True

    # Calculate the 'packageVerificationCode' values for files in packages.
    calculate_package_verification_codes(sbom_data)

    return sbom_data


def create_sbom_for_windows_artifact(
    artifact_path: str, cpython_source_dir: Path | str
) -> SBOM:
    artifact_name = os.path.basename(artifact_path)
    if m := re.match(pat := r"^python-([0-9abrc.]+)(?:-|\.exe|\.zip)", artifact_name):
        cpython_version = m.group(1)
    else:
        raise ValueError(f"Invalid {artifact_name=}, expected {pat!r}")

    if not cpython_source_dir:
        raise ValueError("Must specify --cpython-source-dir for Windows artifacts")
    cpython_source_dir = Path(cpython_source_dir)

    # Start with the CPython source SBOM as a base
    with (cpython_source_dir / "Misc/externals.spdx.json").open() as f:
        sbom_data: SBOM = json.loads(f.read())

    sbom_data["relationships"] = []
    sbom_data["files"] = []

    # Add all the packages from the source SBOM
    # We want to skip the file information because
    # the files aren't available in Windows artifacts.
    with (cpython_source_dir / "Misc/sbom.spdx.json").open() as f:
        source_sbom_data = json.loads(f.read())
        for sbom_package in source_sbom_data["packages"]:
            # Update the SPDX ID to avoid collisions with
            # the 'externals' SBOM.
            sbom_package["SPDXID"] = spdx_id(
                f"SPDXRef-PACKAGE-{sbom_package['name']}-{sbom_package['versionInfo']}"
            )
            sbom_data["packages"].append(sbom_package)

    create_cpython_sbom(
        sbom_data, cpython_version=cpython_version, artifact_path=artifact_path
    )
    sbom_cpython_package_spdx_id = spdx_id("SPDXRef-PACKAGE-cpython")

    # The Windows embed artifacts don't contain pip/ensurepip,
    # but the MSI artifacts do. Add pip for MSI installers.
    if artifact_name.endswith(".exe"):

        # Find the pip wheel in ensurepip in the source code
        for pathname in os.listdir(cpython_source_dir / "Lib/ensurepip/_bundled"):
            if pathname.startswith("pip-") and pathname.endswith(".whl"):
                pip_wheel_filename = pathname
                pip_wheel_bytes = (
                    cpython_source_dir / f"Lib/ensurepip/_bundled/{pathname}"
                ).read_bytes()
                break
        else:
            raise ValueError("Could not find pip wheel in 'Lib/ensurepip/_bundled/...'")

        create_pip_sbom_from_wheel(
            sbom_data,
            pip_wheel_filename=pip_wheel_filename,
            pip_wheel_bytes=pip_wheel_bytes,
        )

    # Final relationship, this SBOM describes the CPython package.
    sbom_data["relationships"].append(
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relatedSpdxElement": sbom_cpython_package_spdx_id,
            "relationshipType": "DESCRIBES",
        }
    )

    # Apply the 'supplier' tag to every package since we're shipping
    # the package in the artifact itself. Originator field is used for maintainers.
    for sbom_package in sbom_data["packages"]:
        sbom_package["supplier"] = "Organization: Python Software Foundation"
        # Source packages have been compiled.
        if sbom_package["primaryPackagePurpose"] == "SOURCE":
            sbom_package["primaryPackagePurpose"] = "LIBRARY"

    return sbom_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpython-source-dir", default=None)
    parser.add_argument("artifacts", nargs="+")
    parsed_args = parser.parse_args(sys.argv[1:])

    artifact_paths = parsed_args.artifacts
    cpython_source_dir = parsed_args.cpython_source_dir

    for artifact_path in artifact_paths:
        # Windows MSI and Embed artifacts
        if artifact_path.endswith(".exe") or artifact_path.endswith(".zip"):
            sbom_data = create_sbom_for_windows_artifact(
                artifact_path, cpython_source_dir=cpython_source_dir
            )
        # Source artifacts
        else:
            sbom_data = create_sbom_for_source_tarball(artifact_path)

        # Normalize SBOM data for reproducibility.
        normalize_sbom_data(sbom_data)

        # Check SBOM for validity.
        check_sbom_data(sbom_data)

        with open(artifact_path + ".spdx.json", mode="w") as f:
            f.truncate()
            f.write(json.dumps(sbom_data, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
