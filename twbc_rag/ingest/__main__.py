"""`python -m twbc_rag.ingest --pcodes D0070109` runs the ingest command."""

from __future__ import annotations

import sys

from twbc_rag.cli import app


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        sys.argv.insert(1, "ingest")
    app()


if __name__ == "__main__":
    main()
