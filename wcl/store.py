"""The cache as tables: every Warcraft Logs response ever fetched, in one SQLite file.

The on-disk cache (cache/<report code>/<hash>.json) is the source of truth and is
never re-fetched; this module turns it into queryable tables so a new analysis can
start from data already downloaded:

    reports        code, title, zone, start/end time
    fights         one row per pull (kills and wipes) with boss, difficulty, outcome
    encounters     boss id -> name and raid zone
    actors         players and pets per report (pets with their owner)
    fight_players  who was in each pull: role, spec, item level, server (playerDetails)
    player_stats   per pull, per wipe cutoff, per table (DamageDone, Healing,
                   DamageTaken, ...): total, active time, overheal/absorbed
    abilities      the per-ability breakdown behind each player_stats row
                   (damage done by spell, healing by spell, damage taken by source)
    deaths         every death: when, killing blow, overkill

Usage:
    python -m wcl.store              rebuild data/wcl.sqlite from the cache
    python -m wcl.store --migrate    also label old cache files (one-time)

run.py rebuilds the store after every run, so it always matches the cache.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from wcl.fetch import (
    ENCOUNTER_ZONE_QUERY,
    FIGHTS_QUERY,
    PET_ACTORS_QUERY,
    PLAYER_ACTORS_QUERY,
    PLAYER_DETAILS_QUERY,
    TABLE_QUERY,
    cache_key,
    is_envelope,
    write_cached,
)

DEFAULT_DB = "data/wcl.sqlite"
WORLD_DIR = "_world"
# Old cache files carry no label, so the migration recomputes their names from
# every query that could have produced them.
ALL_TABLE_TYPES = ["DamageDone", "Healing", "DamageTaken", "Deaths", "Interrupts", "Dispels"]
CUTOFF_CANDIDATES = [None] + list(range(0, 21))

SCHEMA = """
CREATE TABLE reports (
    code TEXT PRIMARY KEY, title TEXT, zone TEXT, start_time INTEGER, end_time INTEGER
);
CREATE TABLE fights (
    code TEXT, fight_id INTEGER, encounter_id INTEGER, encounter TEXT, difficulty INTEGER,
    kill INTEGER, start_time INTEGER, end_time INTEGER, abs_start INTEGER, duration_ms INTEGER,
    fight_percentage REAL, average_item_level REAL,
    PRIMARY KEY (code, fight_id)
);
CREATE TABLE encounters (encounter_id INTEGER PRIMARY KEY, name TEXT, zone TEXT);
CREATE TABLE actors (
    code TEXT, actor_id INTEGER, kind TEXT, name TEXT, class TEXT, server TEXT, pet_owner INTEGER,
    PRIMARY KEY (code, actor_id)
);
CREATE TABLE fight_players (
    code TEXT, fight_id INTEGER, actor_id INTEGER, name TEXT, server TEXT, region TEXT,
    class TEXT, spec TEXT, role TEXT, min_item_level REAL, max_item_level REAL,
    potion_use INTEGER, healthstone_use INTEGER,
    PRIMARY KEY (code, fight_id, actor_id)
);
CREATE TABLE player_stats (
    code TEXT, fight_id INTEGER, cutoff INTEGER, data_type TEXT, actor_id INTEGER,
    name TEXT, class TEXT, spec TEXT, item_level REAL, total REAL, total_reduced REAL,
    active_time INTEGER, overheal REAL, total_time INTEGER,
    PRIMARY KEY (code, fight_id, cutoff, data_type, actor_id)
);
CREATE TABLE abilities (
    code TEXT, fight_id INTEGER, cutoff INTEGER, data_type TEXT, actor_id INTEGER,
    ability_guid INTEGER, ability TEXT, school INTEGER, total REAL, total_reduced REAL
);
CREATE TABLE deaths (
    code TEXT, fight_id INTEGER, cutoff INTEGER, actor_id INTEGER, name TEXT, class TEXT,
    timestamp INTEGER, time_into_fight_ms INTEGER, killing_blow TEXT, killing_blow_guid INTEGER,
    overkill REAL, damage_taken_window REAL
);
CREATE INDEX abilities_by_player ON abilities (code, fight_id, actor_id);
CREATE INDEX fights_by_boss ON fights (encounter_id, difficulty);
CREATE INDEX stats_by_name ON player_stats (name);
CREATE VIEW pulls AS
    SELECT f.*, r.start_time + f.start_time AS started_at, e.zone
    FROM fights f JOIN reports r USING (code) LEFT JOIN encounters e USING (encounter_id);
"""


# --- Labelling old cache files --------------------------------------------


def migrate_cache(cache_dir: str | Path) -> tuple[int, list[Path]]:
    """Wrap unlabelled cache files in the {"query", "variables", "data"} envelope by
    recomputing the hash of every query that could have produced them. Returns
    (files labelled, files that matched nothing)."""
    cache_dir = Path(cache_dir)
    labelled, unknown = 0, []
    for report_dir in sorted(p for p in cache_dir.iterdir() if p.is_dir()):
        pending = {p.stem: p for p in report_dir.glob("*.json") if not _labelled(p)}
        if not pending:
            continue
        for query, variables in _candidates(report_dir, pending):
            path = pending.pop(cache_key(query, variables), None)
            if path is not None:
                write_cached(path, query, variables, json.loads(path.read_text(encoding="utf-8")))
                labelled += 1
            if not pending:
                break
        unknown += pending.values()
    return labelled, unknown


def _labelled(path: Path) -> bool:
    return is_envelope(json.loads(path.read_text(encoding="utf-8")))


def _candidates(report_dir: Path, pending: dict[str, Path]):
    code = report_dir.name
    if code == WORLD_DIR:
        # Boss ids come from every report's fight list.
        ids = set()
        for fights_file in report_dir.parent.glob("*/*.json"):
            data = _data(fights_file)
            report = (data.get("reportData") or {}).get("report") or {}
            ids |= {f["encounterID"] for f in report.get("fights") or [] if f.get("encounterID")}
        for boss in sorted(ids):
            yield ENCOUNTER_ZONE_QUERY, {"id": boss}
        return
    for query in (FIGHTS_QUERY, PLAYER_ACTORS_QUERY, PET_ACTORS_QUERY):
        yield query, {"code": code}
    fights_path = report_dir / f"{cache_key(FIGHTS_QUERY, {'code': code})}.json"
    if not fights_path.exists():
        return
    for fight in _data(fights_path)["reportData"]["report"]["fights"] or []:
        yield PLAYER_DETAILS_QUERY, {"code": code, "ids": [fight["id"]]}
        for data_type in ALL_TABLE_TYPES:
            for cutoff in CUTOFF_CANDIDATES:
                yield TABLE_QUERY, {"code": code, "ids": [fight["id"]], "dt": data_type, "cutoff": cutoff}


def _data(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["data"] if is_envelope(payload) else payload


# --- Building the database --------------------------------------------------


def build_store(cache_dir: str | Path, db_path: str | Path = DEFAULT_DB) -> dict[str, int]:
    """Rebuild the SQLite store from every labelled cache file. Written to a temp
    file and swapped in, so a failed build never leaves a half-empty store.
    Returns row counts per table."""
    cache_dir, db_path = Path(cache_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        fight_starts: dict[tuple[str, int], int] = {}
        files = sorted(cache_dir.glob("*/*.json"))
        envelopes = [json.loads(p.read_text(encoding="utf-8")) for p in files]
        envelopes = [e for e in envelopes if is_envelope(e)]
        # Fights first: deaths need each pull's start time.
        envelopes.sort(key=lambda e: e["query"] != "fights")
        for env in envelopes:
            _ingest(con, env, fight_starts)
        con.commit()
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("reports", "fights", "encounters", "actors", "fight_players",
                          "player_stats", "abilities", "deaths")
        }
    finally:
        con.close()
    tmp.replace(db_path)
    return counts


def _ingest(con: sqlite3.Connection, env: dict, fight_starts: dict) -> None:
    query, variables, data = env["query"], env["variables"], env["data"]
    if query == "encounter":
        encounter = (data.get("worldData") or {}).get("encounter")
        if encounter:
            con.execute("INSERT OR REPLACE INTO encounters VALUES (?, ?, ?)", (
                variables["id"], encounter["name"], (encounter.get("zone") or {}).get("name"),
            ))
        return

    code = variables["code"]
    report = data["reportData"]["report"]
    if query == "fights":
        con.execute("INSERT OR REPLACE INTO reports VALUES (?, ?, ?, ?, ?)", (
            code, report["title"], (report.get("zone") or {}).get("name"),
            report["startTime"], report["endTime"],
        ))
        for f in report["fights"] or []:
            fight_starts[(code, f["id"])] = f["startTime"]
            con.execute("INSERT OR REPLACE INTO fights VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                code, f["id"], f["encounterID"], f["name"], f["difficulty"], int(bool(f["kill"])),
                f["startTime"], f["endTime"], report["startTime"] + f["startTime"],
                f["endTime"] - f["startTime"], f.get("fightPercentage"), f.get("averageItemLevel"),
            ))
    elif query in ("player_actors", "pet_actors"):
        kind = "Player" if query == "player_actors" else "Pet"
        for a in report["masterData"]["actors"] or []:
            con.execute("INSERT OR REPLACE INTO actors VALUES (?,?,?,?,?,?,?)", (
                code, a["id"], kind, a["name"], a.get("subType"), a.get("server"), a.get("petOwner"),
            ))
    elif query == "player_details":
        payload = report["playerDetails"]
        details = payload.get("data", {}).get("playerDetails", payload)
        fight_id = variables["ids"][0]
        for bucket, role in (("tanks", "tank"), ("healers", "healer"), ("dps", "dps")):
            for p in details.get(bucket) or []:
                specs = p.get("specs") or []
                spec = max(specs, key=lambda s: s.get("count", 0))["spec"] if specs else None
                con.execute("INSERT OR REPLACE INTO fight_players VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    code, fight_id, p["id"], p["name"], p.get("server"), p.get("region"), p.get("type"),
                    spec, role, p.get("minItemLevel"), p.get("maxItemLevel"),
                    p.get("potionUse"), p.get("healthstoneUse"),
                ))
    elif query == "table":
        _ingest_table(con, code, variables, report["table"]["data"], fight_starts)


def _spec(icon: str | None) -> str | None:
    return icon.split("-", 1)[1] if icon and "-" in icon else None


def _ingest_table(con, code: str, variables: dict, table: dict, fight_starts: dict) -> None:
    fight_id, data_type, cutoff = variables["ids"][0], variables["dt"], variables.get("cutoff")
    entries = table.get("entries") or []
    if data_type == "Deaths":
        start = fight_starts.get((code, fight_id))
        for e in entries:
            kb = e.get("killingBlow") or {}
            con.execute("INSERT INTO deaths VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                code, fight_id, cutoff, e.get("id"), e.get("name"), e.get("type"), e.get("timestamp"),
                e["timestamp"] - start if start is not None and e.get("timestamp") is not None else None,
                kb.get("name"), kb.get("guid"), e.get("overkill"), (e.get("damage") or {}).get("total"),
            ))
        return
    for e in entries:
        if not isinstance(e, dict) or "id" not in e:
            continue  # Interrupts/Dispels nest their players differently; not fetched today
        con.execute("INSERT OR REPLACE INTO player_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            code, fight_id, cutoff, data_type, e["id"], e.get("name"), e.get("type"), _spec(e.get("icon")),
            e.get("itemLevel"), e.get("total"), e.get("totalReduced"), e.get("activeTime"),
            e.get("overheal"), table.get("totalTime"),
        ))
        con.executemany("INSERT INTO abilities VALUES (?,?,?,?,?,?,?,?,?,?)", [
            (code, fight_id, cutoff, data_type, e["id"], a.get("guid"), a.get("name"), a.get("type"),
             a.get("total"), a.get("totalReduced"))
            for a in e.get("abilities") or []
        ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the SQLite store from the WCL cache")
    parser.add_argument("--cache", default="cache")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--migrate", action="store_true", help="Label old cache files first (one-time)")
    args = parser.parse_args(argv)
    if args.migrate:
        labelled, unknown = migrate_cache(args.cache)
        print(f"Labelled {labelled} cache files" + (f"; {len(unknown)} matched no known query:" if unknown else ""))
        for path in unknown:
            print(f"  {path}")
    counts = build_store(args.cache, args.db)
    print(f"Store rebuilt at {args.db}: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
