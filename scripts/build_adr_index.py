#!/usr/bin/env python3
"""Regenerate docs/adr/README.md from ADR headings, dates, and statuses."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
DATE = re.compile(r"^\*\*Date:\*\*\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*$", re.MULTILINE)
STATUS = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)
SKIP = {"README.md", "TEMPLATE.md", "index.md"}


@dataclass(frozen=True)
class Adr:
    path: Path
    title: str
    date: str
    status: str


def parse(path: Path) -> Adr:
    text = path.read_text(encoding="utf-8")
    title = TITLE.search(text)
    date = DATE.search(text)
    status = STATUS.search(text)
    missing = [name for name, match in (("title", title), ("date", date), ("status", status)) if match is None]
    if missing:
        raise ValueError(f"{path}: missing {', '.join(missing)}")
    return Adr(path, title.group(1).strip(), date.group(1), status.group(1).strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repo.resolve()
    adr_dir = root / "docs" / "adr"
    records = [parse(path) for path in adr_dir.glob("*.md") if path.name not in SKIP]
    records.sort(key=lambda record: record.title.casefold())
    records.sort(key=lambda record: record.date, reverse=True)

    lines = [
        "# Architectural decisions",
        "",
        "ADRs retain decisions, rationale, status, and rejected alternatives.",
        "Create one with `just adr \"Decision title\"`.",
        "",
        "| Date | Title | Status |",
        "|---|---|---|",
    ]
    lines.extend(f"| {record.date} | [{record.title}]({record.path.name}) | {record.status} |" for record in records)
    output = "\n".join(lines) + "\n"
    index_path = adr_dir / "README.md"
    previous = index_path.read_text(encoding="utf-8") if index_path.exists() else None
    if previous != output:
        index_path.write_text(output, encoding="utf-8")
        print(f"Wrote {index_path.relative_to(root)} ({len(records)} ADRs)")
    else:
        print(f"Unchanged {index_path.relative_to(root)} ({len(records)} ADRs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
