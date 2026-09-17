"""Regenerates docs/*.png from their docs/*.drawio source, via the
draw.io desktop app's own CLI (`drawio --export`) - so the checked-in
PNG (what the README actually embeds - GitHub doesn't render .drawio
XML inline) never has to be exported by hand and can't silently drift
out of sync with the source diagram.

Run after editing any docs/*.drawio file:

    venv\\Scripts\\python tools\\export_diagrams.py

Every docs/*.drawio gets its own same-named .png next to it (so adding
a second diagram later needs no changes here) - re-run and commit both
files together.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# draw.io desktop doesn't add itself to PATH on Windows - these are its
# default install locations (checked in order after PATH itself, so a
# PATH entry - e.g. on macOS/Linux, or a customized Windows install -
# always wins).
CANDIDATE_PATHS = [
    r"C:\Program Files\draw.io\draw.io.exe",
    r"C:\Program Files (x86)\draw.io\draw.io.exe",
]


def find_drawio_exe() -> str:
    on_path = shutil.which("drawio") or shutil.which("draw.io")
    if on_path:
        return on_path
    for candidate in CANDIDATE_PATHS:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "draw.io desktop app not found (checked PATH and the usual Windows "
        "install locations). Install it from "
        "https://github.com/jgraph/drawio-desktop/releases, or edit "
        "CANDIDATE_PATHS in this script if it's installed somewhere else."
    )


def export_one(drawio_exe: str, source: Path) -> Path:
    out_path = source.with_suffix(".png")
    cmd = [drawio_exe, "--export", "--format", "png", "--output", str(out_path), str(source)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"drawio export failed for {source.name}: {result.stderr or result.stdout}")
    return out_path


def main() -> None:
    drawio_exe = find_drawio_exe()
    sources = sorted(DOCS_DIR.glob("*.drawio"))
    if not sources:
        print(f"No .drawio files found in {DOCS_DIR}")
        return
    for source in sources:
        out_path = export_one(drawio_exe, source)
        print(f"{source.relative_to(DOCS_DIR.parent)} -> {out_path.relative_to(DOCS_DIR.parent)}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
