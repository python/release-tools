#! /usr/bin/python3

"""Dump the sizes for the given files, suitable for pasting into content.ht"""

import os
import sys
import hashlib

DOT = '.'


# For consistency with historical use.
sort_order = dict((ext, i) for i, ext in enumerate(
    ('tgz', 'tar.bz2', 'amd64.msi', 'msi', 'dmg')))


def key(filename):
    parts = filename.split(DOT)
    # Try 2 parts first.
    ext = DOT.join(parts[-2:])
    if ext not in sort_order:
        ext = parts[-1]
    # Let KeyError propagate.
    return sort_order[ext]


for filename in sorted(sys.argv[1:], key=key):
    md5 = hashlib.md5()
    with open(filename, 'rb') as fp:
        md5.update(fp.read())
    size = os.stat(filename).st_size
    print('  {}  {:8}  {}'.format(md5.hexdigest(), size, filename))
