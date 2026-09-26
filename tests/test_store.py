"""The SQLite store built from the cache, and labelling of old cache files."""
import json
import sqlite3

import run
from wcl.fetch import FIGHTS_QUERY, PLAYER_DETAILS_QUERY, TABLE_QUERY, cache_key, write_cached
from wcl.store import build_store, migrate_cache

CODE = "ABC123"
FIGHTS = {"reportData": {"report": {
    "title": "Raid night", "startTime": 1_000_000, "endTime": 2_000_000,
    "zone": {"name": "Mythic+ Season 2"},
    "fights": [{"id": 3, "name": "Boss", "encounterID": 77, "kill": True, "difficulty": 4,
                "startTime": 5_000, "endTime": 65_000, "fightPercentage": 0, "averageItemLevel": 320}],
}}}
DAMAGE = {"reportData": {"report": {"table": {"data": {"totalTime": 60_000, "entries": [
    {"name": "Falkien", "id": 7, "type": "Rogue", "icon": "Rogue-Outlaw", "itemLevel": 321,
     "total": 9_000_000, "activeTime": 59_000,
     "abilities": [{"guid": 1, "name": "Dispatch", "total": 6_000_000, "type": 1}]},
]}}}}}
DEATHS = {"reportData": {"report": {"table": {"data": {"entries": [
    {"name": "Falkien", "id": 7, "type": "Rogue", "timestamp": 50_000, "fight": 3, "overkill": 10,
     "killingBlow": {"name": "Big Hit", "guid": 9}, "damage": {"total": 400_000}},
]}}}}}
DETAILS = {"reportData": {"report": {"playerDetails": {"data": {"playerDetails": {
    "dps": [{"name": "Falkien", "id": 7, "type": "Rogue", "server": "Silvermoon", "region": "EU",
             "specs": [{"spec": "Outlaw", "count": 1}], "minItemLevel": 321, "maxItemLevel": 321}],
}}}}}}


def _cache(tmp_path, labelled: bool):
    report_dir = tmp_path / "cache" / CODE
    report_dir.mkdir(parents=True)
    items = [
        (FIGHTS_QUERY, {"code": CODE}, FIGHTS),
        (PLAYER_DETAILS_QUERY, {"code": CODE, "ids": [3]}, DETAILS),
        (TABLE_QUERY, {"code": CODE, "ids": [3], "dt": "DamageDone", "cutoff": 3}, DAMAGE),
        (TABLE_QUERY, {"code": CODE, "ids": [3], "dt": "Deaths", "cutoff": 3}, DEATHS),
    ]
    for query, variables, data in items:
        path = report_dir / f"{cache_key(query, variables)}.json"
        if labelled:
            write_cached(path, query, variables, data)
        else:
            path.write_text(json.dumps(data), encoding="utf-8")
    return tmp_path / "cache"


def test_migration_labels_old_files_and_store_has_every_table(tmp_path):
    cache = _cache(tmp_path, labelled=False)
    labelled, unknown = migrate_cache(cache)
    assert (labelled, unknown) == (4, [])

    db = tmp_path / "wcl.sqlite"
    counts = build_store(cache, db)
    assert counts["fights"] == counts["fight_players"] == counts["player_stats"] == 1
    assert counts["abilities"] == counts["deaths"] == 1

    con = sqlite3.connect(db)
    assert con.execute("SELECT name, spec, total, cutoff FROM player_stats").fetchone() == (
        "Falkien", "Outlaw", 9_000_000, 3,
    )
    assert con.execute("SELECT role, spec FROM fight_players").fetchone() == ("dps", "Outlaw")
    # Death time is measured from the pull's start.
    assert con.execute("SELECT time_into_fight_ms, killing_blow FROM deaths").fetchone() == (45_000, "Big Hit")
    assert con.execute("SELECT started_at FROM pulls").fetchone() == (1_005_000,)


def test_shelf_goes_by_the_boss_not_the_report_tag(monkeypatch):
    monkeypatch.setattr(run, "fetch_encounter_zone", lambda boss, cache_dir: {77: "The Venomous Abyss"}.get(boss))
    report = {"zone": "Mythic+ Season 2", "fights": FIGHTS["reportData"]["report"]["fights"]}
    assert run.shelf_zone(report, "cache") == "The Venomous Abyss"
