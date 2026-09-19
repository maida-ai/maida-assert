#!/usr/bin/env python3
"""Fail closed for the retired writer that executed Maida with write credentials."""

import sys


def main(*args, **kwargs):
    print(
        "error: the single-job write-back interface is retired. Migrate to the "
        "three-job acceptance workflow in README.md: authorize, capture with "
        "contents: read, then write the artifact on a fresh trusted runner.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
