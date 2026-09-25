#!/usr/bin/env python
"""CLI entry point: python run.py <report_or_character_url> [options]."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from collections import Counter

from wcl.fetch import fetch_encounter_zone, fetch_report, parse_report_url
from wcl.library import run_dir, slug, write_catalog
from wcl.metrics import build_player_metrics
from wcl.multi import (
    DIFFICULTIES,
    difficulty_summary,
    encounter_pulls,
    is_character_url,
    list_character_reports,
    merge_pulls,
    parse_character_url,
    parse_difficulty,
    pulls_with_player,
)
from wcl.rank import build_overall_order, propose_cuts, score_players
from wcl.report import (
    build_pdf_context,
    encounter_link,
    render_markdown_table,
    render_pdf,
    write_csv,
)
from wcl.roster import filter_to_roster, load_roster
from wcl.store import build_store
from wcl.writing import write_texts


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_composition(spec: str | None, target: int, config: dict) -> dict:
    default = config["roster"]["composition_default"]
    if not spec:
        tank, healer = default["tank"], default["healer"]
        return {"tank": tank, "healer": healer, "dps": max(0, target - tank - healer)}
    parts = spec.split("/")
    if len(parts) != 3:
        raise ValueError("--composition must be in the form tank/healer/dps, e.g. 2/5/13")
    tank, healer, dps = (int(p) for p in parts)
    return {"tank": tank, "healer": healer, "dps": dps}


def load_character_pulls(args, config: dict, wipe_cutoff: int) -> dict | None:
    """Character mode: one boss across the character's reports, merged into one
    report. Returns None (after printing why) when there is nothing to analyse or
    the difficulty has to be chosen first."""
    character, url_boss = parse_character_url(args.url)
    boss = args.boss or url_boss
    if boss is None:
        raise ValueError("Character mode needs a boss: add ?boss=<encounter id> to the URL or pass --boss")
    cache_dir = config["output"]["cache_dir"]

    print(f"Listing reports with encounter {boss} ...")
    name, codes = list_character_reports(character, boss)
    pulls = encounter_pulls(codes, boss, cache_dir)
    print(f"  {name}: {len(codes)} reports, {len(pulls)} unique pulls after removing duplicate logs")
    if not pulls:
        print("No pulls of that boss were found.", file=sys.stderr)
        return None

    summary = difficulty_summary(pulls)
    if args.difficulty:
        difficulty = parse_difficulty(args.difficulty)
    elif len(summary) == 1:
        difficulty = next(iter(summary))
    else:
        # Only the fight lists have been fetched so far; ask before pulling per-pull data.
        print("\nThese logs cover several difficulties. Choose one with --difficulty:")
        for diff_id, counts in sorted(summary.items()):
            print(f"  {DIFFICULTIES.get(diff_id, diff_id)}: {counts['pulls']} pulls ({counts['kills']} kills)")
        return None
    pulls = [p for p in pulls if p["difficulty"] == difficulty]
    difficulty_name = DIFFICULTIES.get(difficulty, str(difficulty))

    required = None if args.all_pulls else (args.require_player or name)
    if required:
        pulls = pulls_with_player(pulls, required, cache_dir)
    if not pulls:
        print(f"No {difficulty_name} pulls" + (f" with {required} present" if required else ""),
              file=sys.stderr)
        return None

    report = merge_pulls(pulls, cache_dir, wipe_cutoff)
    encounter = pulls[0]["encounter"]
    kills = sum(p["kill"] for p in pulls)
    report["code"] = slug(f"{name} {encounter} {difficulty_name}")
    report["title"] = f"{encounter} ({difficulty_name})"
    report["scope"] = (
        f"Based on combined data from {len(pulls)} pulls ({kills} kills, {len(pulls) - kills} wipes) "
        f"of {encounter} on {difficulty_name}"
        + (f" with {required} present" if required else "")
        + f", across {len(report['sources'])} reports"
    )
    report["sources"] = [
        {
            "url": encounter_link(src["code"], boss, difficulty, wipe_cutoff),
            "label": f"{src['date']:%d %b %Y}, {src['pulls']} pull{'s' if src['pulls'] != 1 else ''} "
                     f"({src['kills']} kill{'s' if src['kills'] != 1 else ''})",
        }
        for src in report["sources"]
    ]
    return report


def shelf_zone(report: dict, cache_dir: str) -> str | None:
    """The raid zone of the bosses analysed (most pulls wins), falling back to
    the report's own zone tag."""
    bosses = Counter(f["encounterID"] for f in report["fights"] if f.get("encounterID"))
    for boss, _ in bosses.most_common():
        zone = fetch_encounter_zone(boss, cache_dir)
        if zone:
            return zone
    return report.get("zone")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Warcraft Logs roster analyst")
    parser.add_argument("url", help="Warcraft Logs report URL, or character URL for one boss across reports")
    parser.add_argument("--target", type=int, default=None, help="Target roster size (default from config.yaml)")
    parser.add_argument("--composition", type=str, default=None, help="tank/healer/dps, e.g. 2/4/14")
    parser.add_argument("--raid-leader", action="append", default=[],
                        help="Raid leader: fixed in the roster, status 'Raid Leader' (repeatable)")
    parser.add_argument("--fixed", "--protect", action="append", default=[], dest="fixed",
                        help="Player fixed in the roster, status 'Fixed' (repeatable)")
    parser.add_argument("--cutoff", type=int, default=None, help="wipeCutoff (default from config.yaml)")
    parser.add_argument("--roster", default=None, help="Core roster file (e.g. team_roster.yaml): rank only its mains")
    parser.add_argument("--boss", type=int, default=None, help="Character mode: encounter id (else ?boss= in the URL)")
    parser.add_argument("--difficulty", default=None, help="Character mode: lfr, normal, heroic or mythic")
    parser.add_argument("--require-player", default=None,
                        help="Character mode: only pulls with this player (default: the character)")
    parser.add_argument("--all-pulls", action="store_true", help="Character mode: don't require any player")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--no-cache", action="store_true", help="Ignore on-disk cache (still writes it)")
    args = parser.parse_args(argv)
    # Player names carry diacritics; don't let a Windows console codepage mangle them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    load_dotenv()
    config = load_config(args.config)

    target = args.target or config["roster"]["target_default"]
    wipe_cutoff = args.cutoff if args.cutoff is not None else config["wcl"]["wipe_cutoff_default"]
    composition = parse_composition(args.composition, target, config)
    raid_leaders = set(args.raid_leader)
    protected_names = raid_leaders | set(args.fixed) | set(config.get("protected_players", []))

    fight_filter = None
    if is_character_url(args.url):
        report = load_character_pulls(args, config, wipe_cutoff)
        if report is None:
            return 2
    else:
        code, fight_filter = parse_report_url(args.url)
        print(f"Fetching report {code}" + (f" (fight {fight_filter})" if fight_filter else "") + " ...")
        report = fetch_report(
            code,
            cache_dir=config["output"]["cache_dir"],
            fight_filter=fight_filter,
            wipe_cutoff=wipe_cutoff,
        )
    code = report["code"]
    print(f"  {report['title']} - {len(report['fights'])} pulls, "
          f"{len(report['player_actors'])} player actors")

    rows = build_player_metrics(report, config)
    if args.roster:
        filtered = filter_to_roster(rows, load_roster(args.roster))
        rows = filtered["rows"]
        print(f"  Core roster: {len(rows)} mains ranked; not on the roster: "
              f"{', '.join(filtered['dropped']) or 'none'}")
        if filtered["missing"]:
            print(f"  Mains with no pulls: {', '.join(filtered['missing'])}")
        for mismatch in filtered["mismatches"]:
            print(f"  Roster mismatch: {mismatch}", file=sys.stderr)
    if not rows:
        print("No players with recorded pulls were found in this report.", file=sys.stderr)
        return 1

    rows = score_players(rows, config)
    rows = build_overall_order(rows, protected_names, composition)
    missing = protected_names - {r["name"] for r in rows}
    if missing:
        print(f"  Fixed players not in the data: {', '.join(sorted(missing))}", file=sys.stderr)
    result = propose_cuts(rows, target, composition, protected_names, config, raid_leaders)
    rows = result["rows"]
    texts = write_texts(rows, result["notes"], composition, config)
    notes = texts["notes"]
    for problem in texts["problems"]:
        print(f"  Writing check: {problem}", file=sys.stderr)

    out_root = Path(config["output"]["out_dir"])
    subject = code if is_character_url(args.url) else report["title"]
    if args.roster:
        subject += " core"
    zone = shelf_zone(report, config["output"]["cache_dir"])
    out_dir = run_dir(out_root, zone, subject, datetime.now())
    write_csv(rows, out_dir / "metrics.csv")

    pdf_cfg = config["output"]["pdf"]
    scope = report.get("scope")
    if scope and args.roster:
        scope += ", core roster only"
    context = build_pdf_context(
        rows,
        report_title=report["title"],
        report_code=code,
        target=target,
        composition=composition,
        wipe_cutoff=wipe_cutoff,
        notes=notes,
        fight_id=fight_filter,
        sources=report.get("sources"),
        scope=scope,
    )
    attempts = render_pdf(
        context,
        out_dir / f"{out_dir.name}.pdf",
        out_dir / f"{out_dir.name}.png",
        max_attempts=pdf_cfg["max_render_attempts"],
        max_pages=pdf_cfg.get("max_pages", 1),
    )
    print(f"  PDF rendered in {attempts} attempt(s): {out_dir / (out_dir.name + '.pdf')}")
    print(f"  Catalogued in {write_catalog(out_root)}")
    store_path = config["output"].get("store")
    if store_path:
        counts = build_store(config["output"]["cache_dir"], store_path)
        print(f"  Data store {store_path}: {counts['fights']} pulls, {counts['player_stats']} player stat rows")

    print()
    print(render_markdown_table(rows))
    print()
    if notes:
        print("Notes:")
        for note in notes:
            print(f"  - {note}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
