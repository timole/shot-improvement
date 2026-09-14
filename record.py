"""CLI entrypoint: records a clip with the Logitech camera / Jabra mic
(or the best available fallback), marks each visible hand's palm with a
green box, and saves raw + annotated clips into recordings/.

Usage:
    venv\\Scripts\\python record.py [duration_seconds]
"""

from __future__ import annotations

import sys

from core.log_setup import get_logger, setup_logging
from core.recorder import record_clip

setup_logging()
logger = get_logger("cli")


def main() -> None:
    # Windows terminals often default to a non-UTF-8 codepage, garbling
    # the ä/ö in this script's Finnish status messages otherwise.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    try:
        result = record_clip(duration_s=duration, on_status=print)
    except Exception as exc:
        logger.exception("CLI record_clip failed")
        print(f"Nauhoitus epäonnistui: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Raakatallenne: {result.raw_path}")
    print(f"Merkitty tallenne: {result.annotated_path}")


if __name__ == "__main__":
    main()
