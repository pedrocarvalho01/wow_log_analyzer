#!/usr/bin/env python
"""CLI entry point: python run.py <report_url> [options]."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from wcl.fetch import fetch_report, parse_report_url
from wcl.metrics import build_player_metrics
from wcl.rank import build_overall_order, propose_cuts, score_players
from wcl.report import build_pdf_context, render_markdown_table, render_pdf, write_csv
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Warcraft Logs roster analyst")
    parser.add_argument("url", help="Warcraft Logs report URL")
    parser.add_argument("--target", type=int, default=None, help="Target roster size (default from config.yaml)")
    parser.add_argument("--composition", type=str, default=None, help="tank/healer/dps, e.g. 2/4/14")
    parser.add_argument("--protect", action="append", default=[], help="Player name to protect (repeatable)")
    parser.add_argument("--cutoff", type=int, default=None, help="wipeCutoff (default from config.yaml)")
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
    protected_names = set(args.protect) | set(config.get("protected_players", []))

    code, fight_filter = parse_report_url(args.url)
    print(f"Fetching report {code}" + (f" (fight {fight_filter})" if fight_filter else "") + " ...")

    report = fetch_report(
        code,
        cache_dir=config["output"]["cache_dir"],
        fight_filter=fight_filter,
        wipe_cutoff=wipe_cutoff,
    )
    print(f"  {report['title']} - {len(report['fights'])} pulls, "
          f"{len(report['player_actors'])} player actors")

    rows = build_player_metrics(report, config)
    if not rows:
        print("No players with recorded pulls were found in this report.", file=sys.stderr)
        return 1

    rows = score_players(rows, config)
    rows = build_overall_order(rows, protected_names, composition)
    result = propose_cuts(rows, target, composition, protected_names, config)
    rows = result["rows"]
    texts = write_texts(rows, result["notes"], composition, config)
    notes = texts["notes"]
    for problem in texts["problems"]:
        print(f"  Writing check: {problem}", file=sys.stderr)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(config["output"]["out_dir"]) / code / run_id
    write_csv(rows, out_dir / "metrics.csv")

    pdf_cfg = config["output"]["pdf"]
    context = build_pdf_context(
        rows,
        report_title=report["title"],
        report_code=code,
        target=target,
        composition=composition,
        wipe_cutoff=wipe_cutoff,
        notes=notes,
        fight_id=fight_filter,
    )
    attempts = render_pdf(
        context,
        out_dir / "roster_review.pdf",
        out_dir / "roster_review.png",
        max_attempts=pdf_cfg["max_render_attempts"],
        max_pages=pdf_cfg.get("max_pages", 1),
    )
    print(f"  PDF rendered in {attempts} attempt(s): {out_dir / 'roster_review.pdf'}")

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
