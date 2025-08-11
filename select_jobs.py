#!/usr/bin/env python3

import argparse

from packaging.version import Version


def output(key: str, value: bool) -> None:
    print(f"{key}={str(value).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", type=Version)
    args = parser.parse_args()
    version = args.version

    # Docs are only built for stable releases or release candidates.
    output("docs", version.pre is None or version.pre[0] == "rc")

    # Android binary releases began in Python 3.14.
    output("android", version.release >= (3, 14))


if __name__ == "__main__":
    main()
