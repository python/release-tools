# Purges the Fastly cache for Windows download files
#
# Usage:
#   py -3 purge.py 3.5.1rc1
#

__author__ = "Steve Dower <steve.dower@python.org>"
__version__ = "1.0.0"

import re
import sys
from urllib.request import Request, urlopen

VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)([A-Za-z_]+\d+)?$")

try:
    m = VERSION_RE.match(sys.argv[1])
    if not m:
        print("Invalid version:", sys.argv[1])
        print('Expected something like "3.5.1rc1"')
        sys.exit(1)
except LookupError:
    print('Missing version argument. Expected something like "3.5.1rc1"')
    sys.exit(1)

URL = f"https://www.python.org/ftp/python/{m.group(1)}/"
REL = m.group(2) or ""

FILES = [
    "core.msi",
    "core_d.msi",
    "core_pdb.msi",
    "dev.msi",
    "dev_d.msi",
    "doc.msi",
    "exe.msi",
    "exe_d.msi",
    "exe_pdb.msi",
    "freethreaded.msi",
    "freethreaded_d.msi",
    "freethreaded_pdb.msi",
    "launcher.msi",
    "lib.msi",
    "lib_d.msi",
    "lib_pdb.msi",
    "path.msi",
    "pip.msi",
    "tcltk.msi",
    "tcltk_d.msi",
    "tcltk_pdb.msi",
    "test.msi",
    "test_d.msi",
    "test_pdb.msi",
    "tools.msi",
    "ucrt.msi",
]
PATHS = [
    f"python-{m.group(0)}.exe",
    f"python-{m.group(0)}-webinstall.exe",
    f"python-{m.group(0)}-amd64.exe",
    f"python-{m.group(0)}-amd64-webinstall.exe",
    f"python-{m.group(0)}-arm64.exe",
    f"python-{m.group(0)}-arm64-webinstall.exe",
    f"python-{m.group(0)}-embed-amd64.zip",
    f"python-{m.group(0)}-embed-win32.zip",
    f"python-{m.group(0)}-embed-arm64.zip",
    *[f"win32{REL}/{f}" for f in FILES],
    *[f"amd64{REL}/{f}" for f in FILES],
    *[f"arm64{REL}/{f}" for f in FILES],
]
PATHS = PATHS + [p + ".asc" for p in PATHS]

print("Purged:")
for n in PATHS:
    u = URL + n
    with urlopen(Request(u, method="PURGE", headers={"Fastly-Soft-Purge": 1})) as r:
        r.read()
    print("  ", u)
