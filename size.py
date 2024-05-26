#! /usr/bin/python3

"""Dump the sizes for the given files, suitable for pasting into content.ht"""

import hashlib
import os
import sys

DOT = '.'


# For consistency with historical use.
sort_order = {ext: i for i, ext in enumerate(
    ('tgz', 'tar.bz2', 'tar.xz', 'pdb.zip', 'amd64.msi', 'msi', 'chm', 'dmg'))}


def ignore(filename: str) -> bool:
    return not any(filename.endswith(DOT + ext) for ext in sort_order)


def key(filename: str) -> int:
    parts = filename.split(DOT)
    # Try 2 parts first.
    ext = DOT.join(parts[-2:])
    if ext not in sort_order:
        ext = parts[-1]
    # Let KeyError propagate.
    return sort_order.get(ext, 9999)


def main() -> None:
    for filename in sorted(sys.argv[1:], key=key):
        if ignore(filename):
            continue
        md5 = hashlib.md5()
        with open(filename, 'rb') as fp:
            md5.update(fp.read())
        size = os.stat(filename).st_size
        print(f'  {md5.hexdigest()}  {size:8}  {filename}')


if __name__ == '__main__':
    main()
