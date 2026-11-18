"""Invoke public SciLink headless Analyze with the reviewed UTF-8 prompt."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt_file", type=Path)
    parser.add_argument("options", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        from scilink.cli.analyze import main as analyze
    except ImportError:
        parser.exit(2, "The selected Python does not have a SciLink build with headless Analyze.\n")
    return analyze(["-p", args.prompt_file.read_bytes().decode("utf-8"), *args.options])


if __name__ == "__main__":
    raise SystemExit(main())
