import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

UPLOAD_URL_PREFIX = os.getenv("UPLOAD_URL_PREFIX", "https://www.python.org/ftp/")
UPLOAD_PATH_PREFIX = os.getenv("UPLOAD_PATH_PREFIX", "/srv/www.python.org/ftp/")
INDEX_URL = os.getenv("INDEX_URL", UPLOAD_URL_PREFIX + "python/index-windows.json")
INDEX_FILE = os.getenv("INDEX_FILE")
# A version will be inserted before the extension later on
MANIFEST_FILE = os.getenv("MANIFEST_FILE")
UPLOAD_HOST = os.getenv("UPLOAD_HOST", "")
UPLOAD_HOST_KEY = os.getenv("UPLOAD_HOST_KEY", "")
UPLOAD_KEYFILE = os.getenv("UPLOAD_KEYFILE", "")
UPLOAD_USER = os.getenv("UPLOAD_USER", "")
NO_UPLOAD = os.getenv("NO_UPLOAD", "no")[:1].lower() in "yt1"
LOCAL_INDEX = os.getenv("LOCAL_INDEX", "no")[:1].lower() in "yt1"


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
        raise RuntimeError(
            f"Could not find {exe} to perform upload. Try setting %{env}% or %PATH%"
        )
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
        stderr=subprocess.STDOUT,
        encoding="ascii",
        errors="replace",
    ) as p:
        out, _ = p.communicate(None)
        if out:
            print(out.encode("ascii", "replace").decode("ascii"))
        if p.returncode:
            raise RunError(p.returncode, out)
        return out


def call_ssh(*args, allow_fail=True):
    if not UPLOAD_HOST or NO_UPLOAD or LOCAL_INDEX:
        print("Skipping", args, "because UPLOAD_HOST is missing")
        return
    try:
        return _run(*_std_args(PLINK), f"{UPLOAD_USER}@{UPLOAD_HOST}", *args)
    except RunError as ex:
        if not allow_fail:
            raise
        return ex.args[1]


def upload_ssh(source, dest):
    if not UPLOAD_HOST or NO_UPLOAD or LOCAL_INDEX:
        print("Skipping upload of", source, "because UPLOAD_HOST is missing")
        return
    _run(*_std_args(PSCP), source, f"{UPLOAD_USER}@{UPLOAD_HOST}:{dest}")
    call_ssh(f"chgrp downloads {dest} && chmod g-x,o+r {dest}")


def download_ssh(source, dest):
    if not UPLOAD_HOST:
        print("Skipping download of", source, "because UPLOAD_HOST is missing")
        return
    Path(dest).parent.mkdir(exist_ok=True, parents=True)
    _run(*_std_args(PSCP), f"{UPLOAD_USER}@{UPLOAD_HOST}:{source}", dest)


def ls_ssh(dest):
    if not UPLOAD_HOST or LOCAL_INDEX:
        print("Skipping ls of", dest, "because UPLOAD_HOST is missing")
        return
    try:
        _run(*_std_args(PSCP), "-ls", f"{UPLOAD_USER}@{UPLOAD_HOST}:{dest}")
    except RunError as ex:
        if not ex.args[1].rstrip().endswith("No such file or directory"):
            raise
        print(dest, "was not found")


def url2path(url):
    if not UPLOAD_URL_PREFIX:
        raise ValueError("%UPLOAD_URL_PREFIX% was not set")
    if not url:
        raise ValueError("Unexpected empty URL")
    if not url.startswith(UPLOAD_URL_PREFIX):
        if LOCAL_INDEX:
            return url
        raise ValueError(f"Unexpected URL: {url}")
    return UPLOAD_PATH_PREFIX + url[len(UPLOAD_URL_PREFIX) :]


def get_hashes(src):
    h = hashlib.sha256()
    with open(src, "rb") as f:
        chunk = f.read(1024 * 1024)
        while chunk:
            h.update(chunk)
            chunk = f.read(1024 * 1024)
    return {"sha256": h.hexdigest()}


def trim_install(install):
    return {
        k: v for k, v in install.items() if k not in ("aliases", "run-for", "shortcuts")
    }


def validate_new_installs(installs):
    ids = [i["id"] for i in installs]
    id_set = set(ids)
    if len(id_set) < len(ids):
        for i in id_set:
            ids.remove(i)
        print("WARNING: Duplicate id fields:", *sorted(set(ids)))


def purge(url):
    if not UPLOAD_HOST or NO_UPLOAD:
        print("Skipping purge of", url, "because UPLOAD_HOST is missing")
        return
    print("Purging", url)
    with urlopen(Request(url, method="PURGE", headers={"Fastly-Soft-Purge": 1})) as r:
        r.read()


def calculate_uploads():
    cwd = Path.cwd()
    for p in sorted(
        [
            *cwd.glob("__install__.*.json"),
            *[p / "__install__.json" for p in cwd.iterdir()],
        ]
    ):
        if not p.is_file():
            continue
        print("Processing", p)
        i = json.loads(p.read_bytes())
        u = urlparse(i["url"])
        src = p.parent / u.path.rpartition("/")[-1]
        dest = url2path(i["url"])
        if LOCAL_INDEX:
            i["url"] = str(src.relative_to(Path.cwd())).replace("\\", "/")
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


def remove_and_insert(index, new_installs):
    new = {(i["id"].casefold(), i["sort-version"].casefold()) for i in new_installs}
    to_remove = [
        x
        for x, i in enumerate(index)
        if (i["id"].casefold(), i["sort-version"].casefold()) in new
    ]
    for i in reversed(to_remove):
        del index[i]
    index[:0] = new_installs
    print("Added", len(new_installs), "entries:")
    for i in new_installs:
        print("-", i["id"], i["sort-version"])
    print("Replaced", len(to_remove), "existing entries")
    print()


def hash_packages(uploads):
    for i, src, *_ in uploads:
        i["hash"] = get_hashes(src)


def number_sortkey(n):
    try:
        return f"{int(n):020}"
    except ValueError:
        return n


def install_sortkey(install):
    key = re.split(r"(\d+)", install["id"])
    ver = re.split(r"(\d+)", install["sort-version"])
    return (
        tuple(number_sortkey(k) for k in key),
        tuple(number_sortkey(k) for k in ver),
    )


def find_missing_from_index(url, installs):
    with urlopen(url) as r:
        x = {install_sortkey(i) for i in json.load(r)["versions"]}
    y = {install_sortkey(i) for i in installs} - x
    return [i for i in installs if install_sortkey(i) in y]


UPLOADS = list(calculate_uploads())

if not UPLOADS:
    print("No files to upload!")
    sys.exit(1)


hash_packages(UPLOADS)


index = {"versions": []}

INDEX_MTIME = 0

if INDEX_FILE:
    INDEX_PATH = url2path(INDEX_URL)

    INDEX_MTIME = int(call_ssh(["stat", "-c", "%Y", INDEX_PATH]) or 0)

    try:
        if not LOCAL_INDEX:
            download_ssh(INDEX_PATH, INDEX_FILE)
    except RunError as ex:
        err = ex.args[1]
        if not err.rstrip().endswith("no such file or directory"):
            raise
    else:
        try:
            with open(INDEX_FILE, encoding="utf-8") as f:
                index = json.load(f)
        except FileNotFoundError:
            pass

print(INDEX_PATH, "mtime =", INDEX_MTIME)


new_installs = [trim_install(i) for i, *_ in UPLOADS]
validate_new_installs(new_installs)
new_installs = sorted(new_installs, key=install_sortkey)
remove_and_insert(index["versions"], new_installs)

if INDEX_FILE:
    INDEX_FILE = Path(INDEX_FILE).absolute()
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f)

if MANIFEST_FILE:
    # Use the sort-version so that the manifest name includes prerelease marks
    MANIFEST_FILE = Path(MANIFEST_FILE).absolute()
    name = f"{MANIFEST_FILE.stem}-{new_installs[0]['sort-version']}.json"
    MANIFEST_FILE = MANIFEST_FILE.with_name(name)
    MANIFEST_URL = new_installs[0]["url"].rpartition("/")[0] + "/" + name
    MANIFEST_PATH = url2path(MANIFEST_URL)

    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        # Include an indent for readability. The release manifest is
        # far more likely to be read by humans than the index.
        json.dump({"versions": new_installs}, f, indent=2)


# Upload last to ensure we've got a valid index first
for i, src, dest, sbom, sbom_dest in UPLOADS:
    print("Uploading", src, "to", dest)
    destdir = dest.rpartition("/")[0]
    call_ssh(f"mkdir {destdir} && chgrp downloads {destdir} && chmod a+rx {destdir}")
    upload_ssh(src, dest)
    if sbom and sbom_dest:
        upload_ssh(sbom, sbom_dest)


# Check that nobody else has published while we were uploading
if INDEX_FILE and INDEX_MTIME:
    mtime = int(call_ssh(["stat", "-c", "%Y", INDEX_PATH]) or 0)
    if mtime > INDEX_MTIME:
        print("##[error]Lost a race with another publish step!")
        print("Expecting mtime", INDEX_MTIME, "but saw", mtime)
        sys.exit(1)


if not NO_UPLOAD:
    if MANIFEST_FILE:
        print("Uploading", MANIFEST_FILE, "to", MANIFEST_URL)
        upload_ssh(MANIFEST_FILE, MANIFEST_PATH)

    if INDEX_FILE:
        print("Uploading", INDEX_FILE, "to", INDEX_URL)
        upload_ssh(INDEX_FILE, INDEX_PATH)

    print("Purging", len(UPLOADS), "uploaded files")
    parents = set()
    for i, *_ in UPLOADS:
        purge(i["url"])
        parents.add(i["url"].rpartition("/")[0] + "/")
    for i in parents:
        purge(i)
    if MANIFEST_URL:
        purge(MANIFEST_URL)
        purge(MANIFEST_URL.rpartition("/")[0] + "/")
    if INDEX_URL:
        purge(INDEX_URL)
        purge(INDEX_URL.rpartition("/")[0] + "/")
        missing = find_missing_from_index(INDEX_URL, [i for i, *_ in UPLOADS])
        if missing:
            print("##[error]Lost a race with another publish step!")
            print("Index at", INDEX_URL, "does not contain installs:")
            for m in missing:
                print(m["id"], m["sort-version"])
            sys.exit(1)
