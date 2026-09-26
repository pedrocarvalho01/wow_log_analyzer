"""Where run outputs are shelved, and the catalog that lists them.

    out/
      CATALOG.md                                   every run, newest first
      <shelf>/                                     one per raid zone, e.g. the-venomous-abyss
        <call number>/                             2026-09-25_1638_<subject>
          <call number>.pdf, <call number>.png, metrics.csv

Call numbers start with the date and time, so a shelf sorts by name into run order,
and the PDF carries the call number so a copy taken out of its folder stays identifiable.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

CATALOG_NAME = "CATALOG.md"
UNSHELVED = "unsorted"
SUBJECT_MAX_LEN = 48
CALL_NUMBER = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_(.+)$")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def subject_slug(text: str) -> str:
    """Short slug for the subject part of a call number, cut at a word boundary."""
    s = slug(text)
    if len(s) > SUBJECT_MAX_LEN:
        s = s[:SUBJECT_MAX_LEN].rsplit("-", 1)[0]
    return s or "run"


def run_dir(out_root: Path, zone: str | None, subject: str, when: datetime) -> Path:
    """A new, unused folder for one run: out/<shelf>/<call number>/."""
    shelf = out_root / (slug(zone) if zone else UNSHELVED)
    base = f"{when:%Y-%m-%d_%H%M}_{subject_slug(subject)}"
    path, n = shelf / base, 2
    while path.exists():
        path, n = shelf / f"{base}-{n}", n + 1
    return path


def write_catalog(out_root: Path) -> Path:
    """Rebuild out/CATALOG.md from what is on the shelves, newest run first."""
    entries = []
    for pdf in out_root.glob("*/*/*.pdf"):
        match = CALL_NUMBER.match(pdf.parent.name)
        if match:
            date, hh, mm, subject = match.groups()
            entries.append((f"{date} {hh}:{mm}", pdf.parent.parent.name, subject, pdf))
    entries.sort(key=lambda e: (e[0], e[3].parent.name), reverse=True)

    lines = [
        "# Roster review catalog",
        "",
        "Every run, newest first. Shelves are raid zones; each run folder is named by its call number.",
        "",
        "| When | Shelf | Subject | Review |",
        "|---|---|---|---|",
    ]
    for when, shelf, subject, pdf in entries:
        link = pdf.relative_to(out_root).as_posix()
        lines.append(f"| {when} | {shelf} | {subject} | [{pdf.name}](<{link}>) |")
    path = out_root / CATALOG_NAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
