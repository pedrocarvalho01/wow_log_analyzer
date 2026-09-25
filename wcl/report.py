"""Markdown table, CSV export, and one-page PDF/PNG rendering."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import jinja2
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from wcl.rank import (
    STATUS_ESSENTIAL,
    STATUS_KEEP,
    STATUS_FIXED,
    STATUS_RAID_LEADER,
    STATUS_REMOVE,
    STATUS_RESERVE,
    display_class_name,
)

# Keyed by display name ("Death Knight"). Priest, Rogue, Hunter and Monk are
# darkened from the official class colours so they stay visible on white.
CLASS_COLORS = {
    "Death Knight": "#C41E3A",
    "Demon Hunter": "#A330C9",
    "Druid": "#FF7C0A",
    "Evoker": "#33937F",
    "Hunter": "#8FB85A",
    "Mage": "#3FC7EB",
    "Monk": "#00C77A",
    "Paladin": "#F48CBA",
    "Priest": "#9A9A9A",
    "Rogue": "#D4C23A",
    "Shaman": "#0070DD",
    "Warlock": "#8788EE",
    "Warrior": "#C69B6D",
}

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _fmt_output(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    if round(value, 1) >= 100:
        return "100%"
    return f"{value:.1f}%"


def _status_slug(status: str) -> str:
    return status.lower().replace(" ", "")


def _surv_class(value: float | None) -> str:
    if value is None:
        return ""
    if value < 90:
        return "bad"
    if value < 95:
        return "warn"
    return ""


_ROLE_DISPLAY = {"dps": "DPS", "healer": "Healer", "tank": "Tank"}


def _role_display(row: dict) -> str:
    return _ROLE_DISPLAY.get(row["role"], row["role"].capitalize())


def _role_label(row: dict) -> str:
    return f"{_role_display(row)} {display_class_name(row['class'])}"


TABLE_HEADERS = ["#", "Player", "Role (Class)", "Output", "Survival", "Active", "Status", "Rationale"]


def render_markdown_table(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(TABLE_HEADERS) + " |", "|" + "---|" * len(TABLE_HEADERS)]
    for row in rows:
        output = _fmt_output(row["output"])
        survival = _fmt_pct(row["survival_pct"])
        active = _fmt_pct(row["active_pct"])
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["rank"]),
                    row["name"],
                    _role_label(row),
                    output,
                    survival,
                    active,
                    row["status"],
                    row["rationale"] or "",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_csv(rows: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank", "name", "class", "spec", "role", "ilvl", "pulls_attended", "total_pulls",
        "output", "output_norm", "active_pct", "survival_pct", "deaths", "early_deaths",
        "damage_taken_total", "damage_taken_rate", "mitigated_pct", "overheal_pct",
        "low_sample", "exclude_reason", "status", "confidence", "near_tie_peer", "rationale",
    ]
    # utf-8-sig so Excel shows names like Välerjar or Enzð correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _composition_summary(rows: list[dict]) -> tuple[dict, dict]:
    """Return (counts, names) for kept players per role, names ordered
    best-performer-first (highest rank number first) to match the reference
    report's composition list."""
    kept = [
        r
        for r in rows
        if r["status"]
        in (STATUS_KEEP, STATUS_ESSENTIAL, STATUS_RAID_LEADER, STATUS_FIXED, STATUS_RESERVE)
    ]
    # Best first; excluded players (support specs, low sample) aren't ranked on merit, so they go last.
    kept.sort(key=lambda r: (not r.get("exclude_reason"), r["rank"]), reverse=True)
    counts = {"tank": 0, "healer": 0, "dps": 0}
    names: dict[str, list[str]] = {"tank": [], "healer": [], "dps": []}
    for r in kept:
        counts[r["role"]] = counts.get(r["role"], 0) + 1
        names.setdefault(r["role"], []).append(r["name"])
    return counts, names


# Fit schedule for the one-page check: the spec's two steps (row padding, then
# body font), then further font reductions for unusually large rosters.
FIT_STEPS = [
    {"cell_pad_mm": 1.0, "body_pt": 8.4},
    {"cell_pad_mm": 0.8, "body_pt": 8.4},
    {"cell_pad_mm": 0.8, "body_pt": 8.0},
    {"cell_pad_mm": 0.6, "body_pt": 7.8},
    {"cell_pad_mm": 0.5, "body_pt": 7.6},
]

MAX_NOTES = 4


def report_link(code: str, wipe_cutoff: int, fight_id: int | None = None) -> str:
    """Warcraft Logs link to the analysed view: all bosses, all difficulties,
    the same wipe cutoff, and the single fight if one was requested."""
    url = f"https://www.warcraftlogs.com/reports/{code}?boss=-2&difficulty=0&cutoff={wipe_cutoff}"
    if fight_id is not None:
        url += f"&fight={fight_id}"
    return url


def encounter_link(code: str, boss: int, difficulty: int, wipe_cutoff: int) -> str:
    """Warcraft Logs link to one boss on one difficulty in a report."""
    return f"https://www.warcraftlogs.com/reports/{code}?boss={boss}&difficulty={difficulty}&cutoff={wipe_cutoff}"


def _pdf_row(row: dict) -> dict:
    cls = display_class_name(row["class"])
    return {
        "rank": row["rank"],
        "name": row["name"],
        "role": _role_display(row),
        "cls": cls,
        "class_hex": CLASS_COLORS.get(cls, "#9A9A9A"),
        "output": _fmt_output(row["output"]),
        "survival": _fmt_pct(row["survival_pct"]),
        "surv_class": _surv_class(row["survival_pct"]),
        "active": _fmt_pct(row["active_pct"]),
        "status": row["status"],
        "status_slug": _status_slug(row["status"]),
        "rationale": row["rationale"] or "",
    }


_ROLE_GROUPS = [("tank", "Tanks"), ("healer", "Healers"), ("dps", "DPS")]

# Within a role: cuts first, then the bench, then everyone staying.
_STATUS_ORDER = {
    STATUS_REMOVE: 0,
    STATUS_RESERVE: 1,
    STATUS_KEEP: 2,
    STATUS_ESSENTIAL: 3,
    STATUS_FIXED: 4,
    STATUS_RAID_LEADER: 5,
}


def _role_groups(rows: list[dict]) -> list[dict]:
    """Players split into Tanks, Healers and DPS sections. Each section lists
    Remove, then Reserve, then Keep (then Essential / Fixed / Raid Leader), and
    worst-first (rank ascending) within each status."""
    groups = []
    known = {role for role, _ in _ROLE_GROUPS}
    extra = sorted({r["role"] for r in rows} - known)
    for role, label in _ROLE_GROUPS + [(r, r.capitalize()) for r in extra]:
        members = sorted(
            (r for r in rows if r["role"] == role),
            key=lambda r: (_STATUS_ORDER.get(r["status"], len(_STATUS_ORDER)), r["rank"]),
        )
        if not members:
            continue
        removed = sum(1 for r in members if r["status"] == STATUS_REMOVE)
        groups.append({
            "label": label,
            "total": len(members),
            "kept": len(members) - removed,
            "removed": removed,
            "players": [_pdf_row(r) for r in members],
        })
    return groups


def build_pdf_context(
    rows: list[dict],
    *,
    report_title: str,
    report_code: str,
    target: int,
    composition: dict[str, int],
    wipe_cutoff: int,
    notes: list[str],
    fight_id: int | None = None,
    sources: list[dict] | None = None,
    scope: str | None = None,
) -> dict:
    """`sources`, when given, replaces the single report link in the footer with
    one line per analysed log: [{"url", "label"}]. `scope` replaces the default
    description of which pulls were analysed."""
    removed = [r["name"] for r in rows if r["status"] == STATUS_REMOVE]
    comp_counts, comp_names = _composition_summary(rows)
    return {
        "report_title": report_title,
        "report_code": report_code,
        "report_url": report_link(report_code, wipe_cutoff, fight_id),
        "sources": sources or [],
        "target": target,
        "cutoff": wipe_cutoff,
        "scope": scope or (
            f"Based on data from a single pull (fight {fight_id})"
            if fight_id is not None
            else "Based on combined data from all pulls (kills and wipes)"
        ),
        "players": [_pdf_row(r) for r in rows],
        "groups": _role_groups(rows),
        "removed": removed,
        "n_tanks": comp_counts["tank"],
        "n_healers": comp_counts["healer"],
        "n_dps": comp_counts["dps"],
        "tanks": comp_names["tank"],
        "healers": comp_names["healer"],
        "dps": comp_names["dps"],
        "notes": notes[:MAX_NOTES],
        "month_year": dt.datetime.now().strftime("%B %Y %H:%M"),
        **FIT_STEPS[0],
    }


def render_pdf(
    context: dict,
    out_pdf_path: str | Path,
    out_png_path: str | Path,
    max_attempts: int = 5,
    max_pages: int = 1,
) -> int:
    """Render the one-pager, stepping through FIT_STEPS until the PDF is a
    single page. If no step fits one page and `max_pages` > 1, steps through
    again with continuation-page margins and accepts up to `max_pages` pages.
    Returns the number of attempts used. Raises RuntimeError if it still
    doesn't fit."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
    )
    template = env.get_template("onepager.html.j2")
    out_pdf_path = Path(out_pdf_path)
    out_png_path = Path(out_png_path)
    out_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    steps = FIT_STEPS[: max(1, min(max_attempts, len(FIT_STEPS)))]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # A roster too long for one page is allowed to spill onto more pages,
            # but only once every one-page fit step has failed.
            passes = [(1, False)] + ([(max_pages, True)] if max_pages > 1 else [])
            attempts = [(limit, multipage, step) for limit, multipage in passes for step in steps]
            for attempt, (page_limit, multipage, step) in enumerate(attempts, start=1):
                html = template.render(**{**context, **step, "multipage": multipage})
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.pdf(path=str(out_pdf_path), format="A4", print_background=True)
                num_pages = len(PdfReader(str(out_pdf_path)).pages)
                if num_pages <= page_limit:
                    page.emulate_media(media="print")
                    page.set_viewport_size({"width": 794, "height": 1123})
                    page.screenshot(path=str(out_png_path), full_page=True)
                    page.close()
                    return attempt
                page.close()
        finally:
            browser.close()

    raise RuntimeError(
        f"Could not render a PDF of at most {max_pages} page(s) after {len(attempts)} attempts "
        f"(still {num_pages} pages at {steps[-1]['body_pt']}pt / {steps[-1]['cell_pad_mm']}mm padding)."
    )
