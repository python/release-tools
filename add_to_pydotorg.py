#!/usr/bin/env python
"""
Script to add ReleaseFile objects for Python releases on the new pydotorg.
To use (RELEASE is something like 3.3.5rc2):

* Copy this script to dl-files (it needs access to all the release files).
  You could also download all files, then you need to use the "--ftp-root"
  argument.

* Make sure all download files are in place in the correct FTP subdirectory.

* Create a new Release object via the Django admin (adding via API is
  currently broken), the name MUST be "Python RELEASE".

* Put an AUTH_INFO variable containing "username:api_key" in your environment.

* Call this script as "python add_to_pydotorg.py RELEASE".

  Each call will remove all previous file objects, so you can call the script
  multiple times.

Georg Brandl, March 2014.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Generator
from os import path
from typing import Any, NoReturn

import requests


# Copied from release.py
def error(*msgs: Any) -> NoReturn:
    print("**ERROR**", file=sys.stderr)
    for msg in msgs:
        print(msg, file=sys.stderr)
    sys.exit(1)


# Copied from release.py
def run_cmd(
    cmd: list[str] | str, silent: bool = False, shell: bool = False, **kwargs: Any
) -> None:
    if shell:
        cmd = " ".join(cmd)
    if not silent:
        print(f"Executing {cmd}")
    try:
        if silent:
            subprocess.check_call(cmd, shell=shell, stdout=subprocess.PIPE, **kwargs)
        else:
            subprocess.check_call(cmd, shell=shell, **kwargs)
    except subprocess.CalledProcessError:
        error(f"{cmd} failed")


try:
    auth_info = os.environ["AUTH_INFO"]
except KeyError:
    print(
        "Please set an environment variable named AUTH_INFO "
        'containing "username:api_key".'
    )
    sys.exit()

download_root = "https://www.python.org/ftp/python/"

tag_cre = re.compile(r"(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:([ab]|rc)(\d+))?$")

headers = {"Authorization": f"ApiKey {auth_info}", "Content-Type": "application/json"}

github_oidc_provider = "https://github.com/login/oauth"
google_oidc_provider = "https://accounts.google.com"

# Update this list when new release managers are added.
release_to_sigstore_identity_and_oidc_issuer = {
    "3.8": ("lukasz@langa.pl", github_oidc_provider),
    "3.9": ("lukasz@langa.pl", github_oidc_provider),
    "3.10": ("pablogsal@python.org", google_oidc_provider),
    "3.11": ("pablogsal@python.org", google_oidc_provider),
    "3.12": ("thomas@python.org", google_oidc_provider),
    "3.13": ("thomas@python.org", google_oidc_provider),
    "3.14": ("hugo@python.org", github_oidc_provider),
}


def get_file_descriptions(
    release: str,
) -> list[tuple[re.Pattern[str], tuple[str, int, bool, str]]]:
    v = minor_version_tuple(release)
    rx = re.compile
    # value is (file "name", OS slug, download button, file "description").
    # OS=None means no ReleaseFile object. Only one matching *file* (not regex)
    # per OS can have download=True.
    return [
        (rx(r"\.tgz$"), ("Gzipped source tarball", "source", False, "")),
        (rx(r"\.tar\.xz$"), ("XZ compressed source tarball", "source", True, "")),
        (
            rx(r"windows-.+\.json"),
            (
                "Windows release manifest",
                "windows",
                False,
                f"Install with 'py install {v[0]}.{v[1]}'",
            ),
        ),
        (
            rx(r"-embed-amd64\.zip$"),
            ("Windows embeddable package (64-bit)", "windows", False, ""),
        ),
        (
            rx(r"-embed-arm64\.zip$"),
            ("Windows embeddable package (ARM64)", "windows", False, ""),
        ),
        (
            rx(r"-arm64\.exe$"),
            ("Windows installer (ARM64)", "windows", False, "Experimental"),
        ),
        (
            rx(r"-amd64\.exe$"),
            ("Windows installer (64-bit)", "windows", v >= (3, 9), "Recommended"),
        ),
        (
            rx(r"-embed-win32\.zip$"),
            ("Windows embeddable package (32-bit)", "windows", False, ""),
        ),
        (rx(r"\.exe$"), ("Windows installer (32-bit)", "windows", v < (3, 9), "")),
        (
            rx(r"-macosx10\.5(_rev\d)?\.(dm|pk)g$"),
            (
                "macOS 32-bit i386/PPC installer",
                "macos",
                False,
                "for Mac OS X 10.5 and later",
            ),
        ),
        (
            rx(r"-macosx10\.6(_rev\d)?\.(dm|pk)g$"),
            (
                "macOS 64-bit/32-bit Intel installer",
                "macos",
                False,
                "for Mac OS X 10.6 and later",
            ),
        ),
        (
            rx(r"-macos(x)?10\.9\.(dm|pk)g$"),
            (
                "macOS 64-bit Intel-only installer",
                "macos",
                False,
                "for macOS 10.9 and later, deprecated",
            ),
        ),
        (
            rx(r"-macos(x)?1[1-9](\.[0-9]*)?\.pkg$"),
            (
                "macOS 64-bit universal2 installer",
                "macos",
                True,
                f"for macOS {'10.13' if v >= (3, 12, 6) else '10.9'} and later",
            ),
        ),
        (
            rx(r"aarch64-linux-android.tar.gz$"),
            ("Android embeddable package (aarch64)", "android", False, ""),
        ),
        (
            rx(r"x86_64-linux-android.tar.gz$"),
            ("Android embeddable package (x86_64)", "android", False, ""),
        ),
    ]


def slug_for(release: str) -> str:
    return base_version(release).replace(".", "") + (
        "-" + release[len(base_version(release)) :]
        if release[len(base_version(release)) :]
        else ""
    )


def sigfile_for(release: str, rfile: str) -> str:
    return download_root + f"{release}/{rfile}.asc"


def md5sum_for(filename: str) -> str:
    return hashlib.md5(
        open(filename, "rb").read(),
    ).hexdigest()


def filesize_for(filename: str) -> int:
    return path.getsize(filename)


def make_slug(text: str) -> str:
    return re.sub("[^a-zA-Z0-9_-]", "", text.replace(" ", "-"))


def base_version(release: str) -> str:
    m = tag_cre.match(release)
    assert m is not None, f"Invalid release: {release}"
    return ".".join(m.groups()[:3])


def minor_version(release: str) -> str:
    m = tag_cre.match(release)
    assert m is not None, f"Invalid release: {release}"
    return ".".join(m.groups()[:2])


def minor_version_tuple(release: str) -> tuple[int, int]:
    m = tag_cre.match(release)
    assert m is not None, f"Invalid release: {release}"
    return int(m.groups()[0]), int(m.groups()[1])


def build_file_dict(
    ftp_root: str,
    release: str,
    rfile: str,
    rel_pk: int,
    file_desc: str,
    os_pk: int,
    add_download: bool,
    add_desc: str,
) -> dict[str, Any]:
    """Return a dictionary with all needed fields for a ReleaseFile object."""
    filename = path.join(ftp_root, base_version(release), rfile)
    d = {
        "name": file_desc,
        "slug": slug_for(release) + "-" + make_slug(file_desc)[:40],
        "os": f"/api/v1/downloads/os/{os_pk}/",
        "release": f"/api/v1/downloads/release/{rel_pk}/",
        "description": add_desc,
        "is_source": os_pk == 3,
        "url": download_root + f"{base_version(release)}/{rfile}",
        "md5_sum": md5sum_for(filename),
        "filesize": filesize_for(filename),
        "download_button": add_download,
    }
    # Upload GPG signature
    if os.path.exists(filename + ".asc"):
        d["gpg_signature_file"] = sigfile_for(base_version(release), rfile)
    # Upload Sigstore signature
    if os.path.exists(filename + ".sig"):
        d["sigstore_signature_file"] = (
            download_root + f"{base_version(release)}/{rfile}.sig"
        )
    # Upload Sigstore certificate
    if os.path.exists(filename + ".crt"):
        d["sigstore_cert_file"] = download_root + f"{base_version(release)}/{rfile}.crt"
    # Upload Sigstore bundle
    if os.path.exists(filename + ".sigstore"):
        d["sigstore_bundle_file"] = (
            download_root + f"{base_version(release)}/{rfile}.sigstore"
        )
    # Upload SPDX SBOM file
    if os.path.exists(filename + ".spdx.json"):
        d["sbom_spdx2_file"] = (
            download_root + f"{base_version(release)}/{rfile}.spdx.json"
        )

    return d


def list_files(
    ftp_root: str, release: str
) -> Generator[tuple[str, str, int, bool, str], None, None]:
    """List all of the release's download files."""
    reldir = base_version(release)
    for rfile in os.listdir(path.join(ftp_root, reldir)):
        if not path.isfile(path.join(ftp_root, reldir, rfile)):
            continue

        if rfile.endswith((".asc", ".sig", ".crt", ".sigstore", ".spdx.json")):
            continue

        prefix, _, rest = rfile.partition("-")

        if prefix.lower() not in ("python", "windows"):
            print(f"    File {reldir}/{rfile} has wrong prefix")
            continue

        if not rest.startswith((release + "-", release + ".")):
            print(f"    File {reldir}/{rfile} has a different version")
            continue

        for rx, info in get_file_descriptions(release):
            if rx.search(rfile):
                yield (rfile, *info)
                break
        else:
            print(f"    File {reldir}/{rfile} not recognized")
            continue


def query_object(base_url: str, objtype: str, **params: Any) -> int:
    """Find an API object by query parameters."""
    uri = base_url + f"downloads/{objtype}/"
    uri += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    resp = requests.get(uri, headers=headers)
    if resp.status_code != 200 or not json.loads(resp.text)["objects"]:
        raise RuntimeError(f"no object for {objtype} params={params!r}")
    obj = json.loads(resp.text)["objects"][0]
    return int(obj["resource_uri"].strip("/").split("/")[-1])


def post_object(base_url: str, objtype: str, datadict: dict[str, Any]) -> int:
    """Create a new API object."""
    resp = requests.post(
        base_url + "downloads/" + objtype + "/",
        data=json.dumps(datadict),
        headers=headers,
    )
    if resp.status_code != 201:
        try:
            info = json.loads(resp.text)
            print(info.get("error_message", "No error message."))
            print(info.get("traceback", ""))
        except:  # noqa: E722
            pass
        print(f"Creating {objtype} failed: {resp.status_code}")
        return -1
    newloc = resp.headers["Location"]
    pk = int(newloc.strip("/").split("/")[-1])
    return pk


def sign_release_files_with_sigstore(
    ftp_root: str, release: str, release_files: list[tuple[str, str, int, bool, str]]
) -> None:
    filenames = [
        ftp_root + f"{base_version(release)}/{rfile}" for rfile, *_ in release_files
    ]

    def has_sigstore_signature(filename: str) -> bool:
        return os.path.exists(filename + ".sigstore") or (
            os.path.exists(filename + ".sig") and os.path.exists(filename + ".crt")
        )

    # Ensure that Sigstore CLI installed on the download server is
    # at least v3.0.0 or later to ensure valid Sigstore bundles are generated.
    try:
        sigstore_version_stdout = subprocess.check_output(
            ["python3", "-m", "sigstore", "--version"]
        )
        sigstore_version_match = re.search(
            r"([0-9][0-9.]*[0-9])", sigstore_version_stdout.decode()
        )
        if not sigstore_version_match:
            error(
                f"Couldn't determine version of Sigstore CLI: "
                f"{sigstore_version_stdout.decode()}"
            )
        sigstore_version = sigstore_version_match.group(1)
        sigstore_major_version = int(sigstore_version.partition(".")[0])
        if sigstore_major_version < 3:
            error(
                f"Sigstore v3 or later must be installed "
                f"(currently {sigstore_version}), "
                f"run: python -m pip install -r requirements.txt"
            )
    except subprocess.CalledProcessError:
        error("Couldn't determine version of Sigstore CLI")
    print(f"Sigstore CLI installed is version v{sigstore_version}")

    # Skip files that already have a signature (likely source distributions)
    unsigned_files = [
        filename for filename in filenames if not has_sigstore_signature(filename)
    ]

    if unsigned_files:
        print("Signing release files with Sigstore")
        for filename in unsigned_files:
            cert_file = filename + ".crt"
            sig_file = filename + ".sig"
            bundle_file = filename + ".sigstore"

            run_cmd(
                [
                    "python3",
                    "-m",
                    "sigstore",
                    "sign",
                    "--oidc-disable-ambient-providers",
                    "--signature",
                    sig_file,
                    "--certificate",
                    cert_file,
                    "--bundle",
                    bundle_file,
                    filename,
                ]
            )

            run_cmd(["chmod", "644", sig_file])
            run_cmd(["chmod", "644", cert_file])
            run_cmd(["chmod", "644", bundle_file])
    else:
        print("All release files already signed with Sigstore")

    # Verify all the files we expect to be signed with sigstore
    # against the documented release manager identities and providers.
    try:
        sigstore_identity_and_oidc_issuer = (
            release_to_sigstore_identity_and_oidc_issuer[minor_version(release)]
        )
    except KeyError:
        error(["No release manager defined for Python release " + release])
    sigstore_identity, sigstore_oidc_issuer = sigstore_identity_and_oidc_issuer

    print("Verifying release files were signed correctly with Sigstore")
    sigstore_verify_argv = [
        "python3",
        "-m",
        "sigstore",
        "verify",
        "identity",
        "--cert-identity",
        sigstore_identity,
        "--cert-oidc-issuer",
        sigstore_oidc_issuer,
    ]
    for filename in filenames:
        filename_crt = filename + ".crt"
        filename_sig = filename + ".sig"
        filename_sigstore = filename + ".sigstore"

        if os.path.exists(filename_sigstore):
            run_cmd(
                sigstore_verify_argv + ["--bundle", filename_sigstore, filename],
                stderr=subprocess.STDOUT,  # Sigstore sends stderr on success.
            )

        # We use an 'or' here to error out if one of the files is missing.
        if os.path.exists(filename_sig) or os.path.exists(filename_crt):
            run_cmd(
                sigstore_verify_argv
                + [
                    "--certificate",
                    filename_crt,
                    "--signature",
                    filename_sig,
                    filename,
                ],
                stderr=subprocess.STDOUT,  # Sigstore sends stderr on success.
            )


def parse_args() -> argparse.Namespace:
    def ensure_trailing_slash(s: str):
        if not s.endswith("/"):
            s += "/"
        return s

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        metavar="URL",
        type=ensure_trailing_slash,
        default="https://www.python.org/api/v1/",
        help="API URL; defaults to %(default)s",
    )
    parser.add_argument(
        "--ftp-root",
        metavar="DIR",
        type=ensure_trailing_slash,
        default="/srv/www.python.org/ftp/python",
        help="FTP root; defaults to %(default)s",
    )
    parser.add_argument(
        "release",
        help="Python version number, e.g. 3.3.5rc2",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rel = args.release
    print("Querying python.org for release", rel)
    rel_pk = query_object(args.base_url, "release", name="Python+" + rel)
    print("Found Release object: id =", rel_pk)

    release_files = list(list_files(args.ftp_root, rel))
    sign_release_files_with_sigstore(args.ftp_root, rel, release_files)
    n = 0
    file_dicts = {}
    for rfile, file_desc, os_slug, add_download, add_desc in release_files:
        if not os_slug:
            continue
        os_pk = query_object(args.base_url, "os", slug=os_slug)
        file_dict = build_file_dict(
            args.ftp_root, rel, rfile, rel_pk, file_desc, os_pk, add_download, add_desc
        )
        key = file_dict["slug"]
        print("Creating ReleaseFile object for", rfile, key)
        if key in file_dicts:
            raise RuntimeError(f"duplicate slug generated: {key}")
        file_dicts[key] = file_dict
    print("Deleting previous release files")
    resp = requests.delete(
        args.base_url + f"downloads/release_file/?release={rel_pk}", headers=headers
    )
    if resp.status_code != 204:
        raise RuntimeError(f"deleting previous releases failed: {resp.status_code}")
    for file_dict in file_dicts.values():
        file_pk = post_object(args.base_url, "release_file", file_dict)
        if file_pk >= 0:
            print("Created as id =", file_pk)
            n += 1
    print(f"Done - {n} files added")


if __name__ == "__main__" and not sys.flags.interactive:
    main()
