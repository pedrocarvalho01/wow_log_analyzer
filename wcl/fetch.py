"""Report -> fights, actors, playerDetails, tables.

Field/argument names below were confirmed via live introspection
(`python -m wcl.introspect`) and live sample calls against a real report on
2026-09-25. Notable findings, all matching the BUILD spec:

- `Report.fights(killType: Encounters)` returns both kills and wipes,
  excluding trash - exactly what's needed.
- `playerDetails(fightIDs, includeCombatantInfo: true)` returns
  `{healers: [...], tanks: [...], dps: [...]}`, each entry having
  `id`, `name`, `type` (class), `specs: [{spec, count}]`, `minItemLevel`,
  `maxItemLevel`.
- `table(fightIDs, dataType, wipeCutoff)` returns
  `{entries: [...], totalTime}`. `entries[].total` is a raw total (not a
  rate) - per-second output is computed in metrics.py as
  `total / (totalTime / 1000)`, matching the web UI's DPS/HPS convention.
- Healing entries include `overheal` separately; `total` is already effective
  (net) healing, so overheal% = overheal / (total + overheal).
- DamageTaken entries carry the absorbed amount in `overheal`; mitigation%
  = absorbed / (total + absorbed), as shown in the web UI.
- Deaths entries are one row per death event (not per player), each with a
  `timestamp` (absolute, same clock as fight `startTime`/`endTime`) and
  `fight` id - first-death time for survival% is
  `min(timestamp) - fight.startTime`.
- Pets appear as separate `table` entries with `type: "Pet"` and no
  `petOwner` field on the entry itself. The owner mapping lives on
  `masterData.actors(type: "Pet")[].petOwner`, and is frequently `null`
  (couldn't be resolved for that pull) - pets with an unresolved owner are
  dropped rather than guessed at.
- `table()` has no per-fight breakdown parameter: each call aggregates over
  whichever `fightIDs` are passed. Per-pull granularity (needed for
  survival% and normalisation) therefore requires one call per fight per
  dataType. This module fetches at that granularity and metrics.py derives
  raid-aggregate totals itself by summing pulls, rather than issuing a
  second redundant aggregate call.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wcl.client import graphql

REPORT_CODE_RE = re.compile(r"/reports/([A-Za-z0-9]+)")

# Interrupts/Dispels are deliberately not fetched: nothing scores utility yet, and
# they cost two API calls per pull.
TABLE_DATA_TYPES = ["DamageDone", "Healing", "DamageTaken", "Deaths"]

FIGHTS_QUERY = """
query ($code: String!) {
  reportData {
    report(code: $code) {
      title
      startTime
      endTime
      zone { name }
      fights(killType: Encounters) {
        id
        name
        encounterID
        kill
        difficulty
        startTime
        endTime
        fightPercentage
        averageItemLevel
      }
    }
  }
}
"""

PLAYER_ACTORS_QUERY = """
query ($code: String!) {
  reportData {
    report(code: $code) {
      masterData {
        actors(type: "Player") { id name type subType server }
      }
    }
  }
}
"""

PET_ACTORS_QUERY = """
query ($code: String!) {
  reportData {
    report(code: $code) {
      masterData {
        actors(type: "Pet") { id name petOwner }
      }
    }
  }
}
"""

PLAYER_DETAILS_QUERY = """
query ($code: String!, $ids: [Int]) {
  reportData {
    report(code: $code) {
      playerDetails(fightIDs: $ids, includeCombatantInfo: true)
    }
  }
}
"""

TABLE_QUERY = """
query ($code: String!, $ids: [Int], $dt: TableDataType!, $cutoff: Int) {
  reportData {
    report(code: $code) {
      table(fightIDs: $ids, dataType: $dt, wipeCutoff: $cutoff)
    }
  }
}
"""


ENCOUNTER_ZONE_QUERY = """
query ($id: Int!) {
  worldData {
    encounter(id: $id) { name zone { name } }
  }
}
"""


def fetch_encounter_zone(encounter_id: int, cache_dir: str = "cache") -> str | None:
    """The raid zone a boss belongs to. A report's own zone is whatever content
    dominated the log (a raid night logged after M+ keys says "Mythic+ Season 2"),
    so shelving goes by the boss instead."""
    result = _Cache("_world", cache_dir).get_or_fetch(ENCOUNTER_ZONE_QUERY, {"id": encounter_id})
    encounter = result["worldData"]["encounter"]
    return encounter["zone"]["name"] if encounter and encounter.get("zone") else None


def parse_report_url(url: str) -> tuple[str, int | None]:
    """Return (report_code, single_fight_id_or_None)."""
    parsed = urlparse(url.strip())
    match = REPORT_CODE_RE.search(parsed.path)
    if not match:
        raise ValueError(f"Could not find a report code in URL: {url}")
    code = match.group(1)
    query = parse_qs(parsed.query)
    fight_param = query.get("fight", [None])[0]
    fight_id = int(fight_param) if fight_param and fight_param.isdigit() else None
    return code, fight_id


# Every cached response is saved as {"query": <name>, "variables": {...}, "data": {...}}
# so wcl.store can turn the cache into tables without knowing the file-name hashes.
QUERY_NAMES = {
    FIGHTS_QUERY: "fights",
    PLAYER_ACTORS_QUERY: "player_actors",
    PET_ACTORS_QUERY: "pet_actors",
    PLAYER_DETAILS_QUERY: "player_details",
    TABLE_QUERY: "table",
    ENCOUNTER_ZONE_QUERY: "encounter",
}


def cache_key(query: str, variables: dict) -> str:
    return hashlib.sha256((query + json.dumps(variables, sort_keys=True)).encode("utf-8")).hexdigest()


def read_cached(path: Path) -> dict:
    """A cached response's data, whether saved with the envelope or (older files) bare."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["data"] if is_envelope(payload) else payload


def is_envelope(payload: dict) -> bool:
    return isinstance(payload, dict) and set(payload) == {"query", "variables", "data"}


def write_cached(path: Path, query: str, variables: dict, data: dict) -> None:
    envelope = {"query": QUERY_NAMES.get(query, "unknown"), "variables": variables, "data": data}
    path.write_text(json.dumps(envelope), encoding="utf-8")


class _Cache:
    def __init__(self, code: str, cache_dir: str):
        self.dir = Path(cache_dir) / code
        self.dir.mkdir(parents=True, exist_ok=True)

    def get_or_fetch(self, query: str, variables: dict) -> dict:
        path = self.dir / f"{cache_key(query, variables)}.json"
        if path.exists():
            return read_cached(path)
        result = graphql(query, variables)
        write_cached(path, query, variables, result)
        return result


def fetch_report_fights(code: str, cache_dir: str = "cache") -> dict:
    """The report's title, start time and encounter pulls (kills and wipes)."""
    return _Cache(code, cache_dir).get_or_fetch(FIGHTS_QUERY, {"code": code})["reportData"]["report"]


def fetch_player_details(code: str, fight_id: int, cache_dir: str = "cache") -> dict:
    """One pull's {"tanks", "healers", "dps"} buckets. Shares fetch_report's cache
    entry, so checking who was in a pull costs nothing extra later."""
    result = _Cache(code, cache_dir).get_or_fetch(PLAYER_DETAILS_QUERY, {"code": code, "ids": [fight_id]})
    payload = result["reportData"]["report"]["playerDetails"]
    # playerDetails is returned wrapped as {"data": {"playerDetails": {...}}}
    return payload.get("data", {}).get("playerDetails", payload)


def fetch_report(
    code: str,
    cache_dir: str = "cache",
    fight_filter: int | None = None,
    wipe_cutoff: int = 3,
    fight_ids: set[int] | None = None,
) -> dict:
    """Fetch everything needed to compute per-player metrics for a report.

    Returns a dict:
      {
        "code", "title", "zone",
        "fights": [ {id, name, encounterID, kill, difficulty, startTime,
                     endTime, fightPercentage, averageItemLevel} ],
        "player_actors": {id: {name, class, server}},
        "pet_owner": {pet_id: owner_player_id},
        "player_details_by_fight": {fight_id: {"healers": [...], ...}},
        "tables": {fight_id: {dataType: table_dict}},
      }
    """
    cache = _Cache(code, cache_dir)

    fights_data = cache.get_or_fetch(FIGHTS_QUERY, {"code": code})
    report = fights_data["reportData"]["report"]
    fights = report["fights"] or []
    if fight_filter is not None:
        fights = [f for f in fights if f["id"] == fight_filter]
        if not fights:
            raise ValueError(f"Fight {fight_filter} not found in report {code}")
    if fight_ids is not None:
        fights = [f for f in fights if f["id"] in fight_ids]

    actors_data = cache.get_or_fetch(PLAYER_ACTORS_QUERY, {"code": code})
    player_actors = {
        a["id"]: {"name": a["name"], "class": a["subType"], "server": a["server"]}
        for a in actors_data["reportData"]["report"]["masterData"]["actors"]
    }

    pets_data = cache.get_or_fetch(PET_ACTORS_QUERY, {"code": code})
    pet_owner = {
        a["id"]: a["petOwner"]
        for a in pets_data["reportData"]["report"]["masterData"]["actors"]
        if a.get("petOwner") is not None
    }

    data_types = TABLE_DATA_TYPES

    player_details_by_fight: dict[int, dict] = {}
    tables: dict[int, dict] = {}
    for fight in fights:
        fid = fight["id"]

        player_details_by_fight[fid] = fetch_player_details(code, fid, cache_dir)

        tables[fid] = {}
        for dt in data_types:
            t_result = cache.get_or_fetch(
                TABLE_QUERY, {"code": code, "ids": [fid], "dt": dt, "cutoff": wipe_cutoff}
            )
            tables[fid][dt] = t_result["reportData"]["report"]["table"]["data"]

    return {
        "code": code,
        "title": report["title"],
        "zone": report["zone"]["name"] if report.get("zone") else None,
        "fights": fights,
        "player_actors": player_actors,
        "pet_owner": pet_owner,
        "player_details_by_fight": player_details_by_fight,
        "tables": tables,
    }
