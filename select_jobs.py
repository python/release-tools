#!/usr/bin/env python3

import argparse

from release import Tag


def output(key: str, value: bool) -> None:
    print(f"{key}={str(value).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", type=Tag)
    args = parser.parse_args()
    version = args.version

    # Docs are only built for stable releases or release candidates.
    output("docs", version.level in ["rc", "f"])

    # Android binary releases began in Python 3.14.
    output("android", version.as_tuple() >= (3, 14))


if __name__ == "__main__":
    main()
