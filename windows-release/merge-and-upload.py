import hashlib
import json
import os
import subprocess

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, Request

UPLOAD_URL_PREFIX = os.getenv("UPLOAD_URL_PREFIX") or "https://www.python.org/ftp/"
UPLOAD_PATH_PREFIX = os.getenv("UPLOAD_PATH_PREFIX") or "/srv/www.python.org/ftp/"
INDEX_URL = os.getenv("INDEX_URL") or f"https://www.python.org/ftp/python/__index_windows__.json"
UPLOAD_HOST = os.getenv("PyDotOrgServer")
UPLOAD_HOST_KEY = os.getenv("PyDotOrgHostKey")
UPLOAD_KEYFILE = os.getenv("UPLOAD_KEYFILE")
UPLOAD_USER = os.getenv("PyDotOrgUsername")
NO_UPLOAD = os.getenv("NO_UPLOAD")

def find_cmd(env, exe):
    cmd = os.getenv(env)
    if cmd:
        return Path(cmd)
    for p in os.getenv("PATH", "").split(";"):
        if p:
            cmd = Path(p) / exe
            if cmd.is_file():
                return cmd
    if UPLOAD_HOST:
        raise RuntimeError(f"Could not find {exe} to perform upload. Try setting %{env}% or %PATH%")
    print(f"Did not find {exe}, but not uploading anyway.")

PLINK = find_cmd("PLINK", "plink.exe")
PSCP = find_cmd("PSCP", "pscp.exe")


def _std_args(cmd):
    if not cmd:
        raise RuntimeError("Cannot upload because command is missing")
    all_args = [cmd, "-batch"]
    if UPLOAD_HOST_KEY:
        all_args.append("-hostkey")
        all_args.append(UPLOAD_HOST_KEY)
    if UPLOAD_KEYFILE:
        all_args.append("-noagent")
        all_args.append("-i")
        all_args.append(UPLOAD_KEYFILE)
    return all_args


class RunError(Exception):
    pass


def _run(*args):
    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="ascii",
        errors="replace",
    ) as p:
        out, err = p.communicate(None)
        if out:
            print(out)
        if err:
            print(err)
        if p.returncode:
            raise RunError(p.returncode, out, err)


def call_ssh(*args, allow_fail=True):
    if not UPLOAD_HOST or NO_UPLOAD:
        print("Skipping", args, "because UPLOAD_HOST is missing")
        return
    try:
        _run(*_std_args(PLINK), f"{UPLOAD_USER}@{UPLOAD_HOST}", *args)
    except RunError:
        if not allow_fail:
            raise


def upload_ssh(source, dest):
    if not UPLOAD_HOST or NO_UPLOAD:
        print("Skipping upload of", source, "because UPLOAD_HOST is missing")
        return
    _run(*_std_args(PSCP), source, f"{UPLOAD_USER}@{UPLOAD_HOST}:{dest}")


def download_ssh(source, dest):
    if not UPLOAD_HOST:
        print("Skipping download of", source, "because UPLOAD_HOST is missing")
        return
    _run(*_std_args(PSCP), f"{UPLOAD_USER}@{UPLOAD_HOST}:{source}", dest)


def ls_ssh(dest):
    if not UPLOAD_HOST:
        print("Skipping ls of", dest, "because UPLOAD_HOST is missing")
        return
    try:
        _run(*_std_args(PSCP), "-ls", f"{UPLOAD_USER}@{UPLOAD_HOST}:{dest}")
    except RunError as ex:
        if not ex.args[2].rstrip().endswith("No such file or directory"):
            raise
        print(dest, "was not found")


def url2path(url):
    if not url.startswith(UPLOAD_URL_PREFIX):
        raise ValueError(f"Unexpected URL: {url}")
    return UPLOAD_PATH_PREFIX + url[len(UPLOAD_URL_PREFIX):]


def get_hashes(src):
    h = hashlib.sha256()
    with open(src, "rb") as f:
        chunk = f.read(1024 * 1024)
        while chunk:
            h.update(chunk)
            chunk = f.read(1024 * 1024)
    return {"sha256": h.hexdigest()}


def purge(url):
    if not UPLOAD_HOST or NO_UPLOAD:
        print("Skipping purge of", url, "because UPLOAD_HOST is missing")
        return
    with urlopen(Request(url, method="PURGE", headers={"Fastly-Soft-Purge": 1})) as r:
        r.read()


def calculate_uploads():
    for p in Path().absolute().glob("__install*.json"):
        i = json.loads(p.read_bytes())
        u = urlparse(i["url"])
        src = Path(u.path.rpartition("/")[-1]).absolute()
        dest = url2path(i["url"])
        sbom = src.with_suffix(".spdx.json")
        sbom_dest = dest.rpartition("/")[0] + sbom.name
        if not sbom.is_file():
            sbom = None
            sbom_dest = None
        yield (
            i,
            src,
            url2path(i["url"]),
            sbom,
            sbom_dest,
        )


def hash_packages(uploads):
    for i, src, *_ in uploads:
        i["hashes"] = get_hashes(src)


UPLOADS = list(calculate_uploads())
hash_packages(UPLOADS)


INDEX_PATH = url2path(INDEX_URL)
try:
    download_ssh(INDEX_PATH, "__index__.json")
except RunError as ex:
    err = ex.args[2]
    if not err.rstrip().endswith("no such file or directory"):
        raise
    index = {"versions": []}
else:
    with open("__index__.json", "r", encoding="utf-8") as f:
        index = json.load(f)


# TODO: Sort?
index["versions"][:0] = [i for i, *_ in UPLOADS]


with open("__index__.json", "w", encoding="utf-8") as f:
    # Include an indent for sanity while testing.
    # We should probably remove it later for the size benefits.
    json.dump(index, f, indent=1)

print("Merged", len(UPLOADS), "entries")


# Upload last to ensure we've got a valid index first
for i, src, dest, sbom, sbom_dest in UPLOADS:
    print("Uploading", src, "to", dest)
    destdir = dest.rpartition("/")[0]
    call_ssh("mkdir", destdir, "&&", "chgrp", "downloads", destdir, "&&", "chmod", "a+rx", destdir)
    upload_ssh(src, dest)
    call_ssh("chgrp", "downloads", dest, "&&", "chmod", "g-x,o+r", dest)
    if sbom and sbom_dest:
        upload_ssh(sbom, sbom_dest)
        call_ssh("chgrp", "downloads", sbom_dest, "&&", "chmod", "g-x,o+r", sbom_dest)


print("Uploading __index__.json to", INDEX_URL)
upload_ssh("__index__.json", INDEX_PATH)


print("Purging", len(UPLOADS), "uploaded files")
for i, src, dest in UPLOADS:
    purge(i["url"])
purge(INDEX_URL)
