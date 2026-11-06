#!/usr/bin/env python3
"""Create a dated ADR without replacing an existing record."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("title")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.repo.resolve()
    adr_dir = root / "docs" / "adr"
    template_path = adr_dir / "TEMPLATE.md"
    template = template_path.read_text(encoding="utf-8")
    slug = re.sub(r"[^a-z0-9]+", "_", args.title.lower()).strip("_")
    if not slug:
        raise SystemExit("title must contain a letter or number")

    date = dt.date.today().isoformat()
    path = adr_dir / f"{date}-{slug}.md"
    suffix = 1
    while path.exists():
        path = adr_dir / f"{date}-{slug}-{suffix}.md"
        suffix += 1

    text = template.replace("ADR_TITLE", args.title).replace("ADR_DATE", date)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    print(f"Created {path.relative_to(root)}")

    index_script = root / "scripts" / "build_adr_index.py"
    if index_script.exists():
        subprocess.run([sys.executable, str(index_script), "--repo", str(root)], check=True)
    else:
        print(f"Index not rebuilt: {index_script.relative_to(root)} is absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
