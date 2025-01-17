import hashlib
import json
import os
import subprocess

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, Request

INDEX_PATH = os.getenv("INDEX_PATH", "ftp/downloads/__index_windows__.json")
INDEX_URL = os.getenv("INDEX_URL") or f"https://www.python.org/{INDEX_PATH}"
UPLOAD_HOST = os.getenv("PyDotOrgServer")


def call_ssh(args):
    if not UPLOAD_HOST:
        print("Skipping", args, "because UPLOAD_HOST is missing")
        return
    subprocess.check_output(args)


def get_hashes(src):
    h = hashlib.sha256()
    with open(src, "rb") as f:
        chunk = f.read(1024 * 1024)
        while chunk:
            h.update(chunk)
            chunk = f.read(1024 * 1024)
    return {"sha256": h.hexdigest()}


def purge(url):
    if not UPLOAD_HOST:
        print("Skipping purge of", url, "because UPLOAD_HOST is missing")
        return
    with urlopen(Request(url, method="PURGE", headers={"Fastly-Soft-Purge": 1})) as r:
        r.read()


def calculate_uploads():
    for p in Path().absolute().glob("__install*.json"):
        i = json.loads(p.read_bytes())
        u = urlparse(i["url"])
        yield (
            i,
            Path(u.path.rpartition("/")[-1]).absolute(),
            u.path,
        )

def upload_files(uploads):
    for i, src, dest in uploads:
        print("Uploading", src, "to", dest)
        call_ssh([...])

def purge_files(uploads):
    for i, src, dest in uploads:
        purge(i["url"])

def hash_packages(uploads):
    for i, src, dest in uploads:
        i["hashes"] = get_hashes(src)


UPLOADS = list(calculate_uploads())
hash_packages(UPLOADS)


try:
    with open("__index__.json", "rb") as f:
        index = json.load(f)
except FileNotFoundError:
    index = {"versions": []}


# TODO: Sort?
index["versions"][:0] = [i[0] for i in UPLOADS]

with open("__index__.json", "wb") as f:
    # Include an indent for sanity while testing.
    # We should probably remove it later for the size benefits.
    json.dump(f, index, indent=1)

print("Merged", len(UPLOADS), "entries")

# Upload last to ensure we've got a valid index first
upload_files(UPLOADS)

print("Uploading __index__.json to python.org")
run_ssh([...])

purge_files(UPLOADS)
purge(INDEX_URL)
