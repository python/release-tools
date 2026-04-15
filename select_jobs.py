#!/usr/bin/env python3

import argparse

from release import Tag


def output(key: str, value: bool) -> None:
    print(f"{key}={str(value).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", type=Tag)
    parser.add_argument(
        "--test",
        action="store_true",
        help="Enable all jobs for testing",
    )
    args = parser.parse_args()
    version = args.version

    if args.test:
        # When testing the workflow itself (push/PR),
        # enable all jobs for full coverage.
        output("docs", True)
        output("android", True)
        output("ios", True)
        return

    # Docs are only built for stable releases or release candidates.
    output("docs", version.level in ["rc", "f"])

    # Android binary releases began in Python 3.14.
    output("android", version.as_tuple() >= (3, 14))

    # iOS binary releases began in Python 3.15.
    output("ios", version.as_tuple() >= (3, 15))


if __name__ == "__main__":
    main()
