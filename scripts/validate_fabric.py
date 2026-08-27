"""Standalone CLI mirroring `fat config validate` — validate fabric.yml against the schema.

Self-contained: only depends on `_fabric_config` (this directory), `pydantic`, and `pyyaml`.
"""

import argparse
from pathlib import Path
import sys

from _fabric_config import validate_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a fabric.yml file against the schema.")
    parser.add_argument(
        "--path", "-p", type=Path, default=Path("fabric.yml"), help="Path to fabric.yml"
    )
    args = parser.parse_args()

    path: Path = args.path
    if not path.exists():
        print(f"Error: {path} not found.", file=sys.stderr)
        sys.exit(1)

    valid, errors = validate_config(path)

    if valid:
        print(f"{path}: valid")
    else:
        print(f"{path}: invalid", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
