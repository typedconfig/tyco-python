import argparse
import json
import sys
from pathlib import Path

from ._parser import TycoParseError, load


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tyco",
        description="Parse Tyco configuration files and emit their JSON representation.",
    )
    parser.add_argument(
        "path",
        help="Path to a .tyco file or directory containing Tyco files.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "python"),
        default="json",
        help="Output format (default: json).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        print(f"Error: '{target}' does not exist.", file=sys.stderr)
        return 1

    try:
        context = load(str(target))
    except TycoParseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    data = context.as_json()
    if args.format == "json":
        indent = 2 if args.pretty else None
        json.dump(data, sys.stdout, indent=indent)
        sys.stdout.write("\n")
    else:
        print(data)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
