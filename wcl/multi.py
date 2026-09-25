"""One boss across many reports: a character's logs -> unique pulls -> one merged report.

The same pull is often logged by several raiders, and a character's reports mix
difficulties and groups, so pulls are deduplicated by their absolute start time and
filtered by difficulty and by a player who must be present. The surviving pulls are
merged into a single report dict that build_player_metrics accepts unchanged:
players are re-keyed by (name, server) and fights get new sequential ids.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from wcl.client import graphql
from wcl.fetch import fetch_player_details, fetch_report, fetch_report_fights

DIFFICULTIES = {1: "LFR", 3: "Normal", 4: "Heroic", 5: "Mythic"}
DIFFICULTY_BY_NAME = {name.lower(): diff_id for diff_id, name in DIFFICULTIES.items()}

# Two logs of one pull start within a few seconds of each other.
SAME_PULL_WINDOW_MS = 30_000
# 50 reports per page with nested fights stays under the API's 50000 complexity cap.
REPORTS_PAGE_SIZE = 50
# Pets and NPCs get merged ids from here so they never collide with players.
FIRST_NON_PLAYER_ID = 10_000_000

CHARACTER_REPORTS_QUERY = """
query ($id: Int, $name: String, $server: String, $region: String, $boss: Int, $page: Int, $limit: Int) {
  characterData {
    character(id: $id, name: $name, serverSlug: $server, serverRegion: $region) {
      name
      recentReports(limit: $limit, page: $page) {
        last_page
        data { code fights(encounterID: $boss) { id } }
      }
    }
  }
}
"""

_CHARACTER_ID_RE = re.compile(r"/character/id/(\d+)")
_CHARACTER_NAME_RE = re.compile(r"/character/([a-z]{2})/([^/]+)/([^/?#]+)", re.IGNORECASE)


def is_character_url(url: str) -> bool:
    return "/character/" in urlparse(url.strip()).path


def parse_character_url(url: str) -> tuple[dict, int | None]:
    """Return (character lookup variables, boss id from ?boss= or None)."""
    parsed = urlparse(url.strip())
    boss_param = parse_qs(parsed.query).get("boss", [None])[0]
    boss = int(boss_param) if boss_param and boss_param.lstrip("-").isdigit() else None
    if boss is not None and boss <= 0:
        boss = None  # boss=-2 / boss=-3 are "all bosses" / "trash" views, not an encounter
    if match := _CHARACTER_ID_RE.search(parsed.path):
        return {"id": int(match.group(1))}, boss
    if match := _CHARACTER_NAME_RE.search(parsed.path):
        region, server, name = match.groups()
        return {"region": region.upper(), "server": server.lower(), "name": name}, boss
    raise ValueError(f"Could not find a character in URL: {url}")


def parse_difficulty(value: str) -> int:
    value = value.strip().lower()
    if value.isdigit() and int(value) in DIFFICULTIES:
        return int(value)
    if value in DIFFICULTY_BY_NAME:
        return DIFFICULTY_BY_NAME[value]
    raise ValueError(f"Unknown difficulty {value!r}; use one of {', '.join(DIFFICULTY_BY_NAME)}")


def list_character_reports(character: dict, boss: int) -> tuple[str, list[str]]:
    """(character name, codes of the character's reports that include the boss).
    Not cached: the list grows every raid night."""
    codes: list[str] = []
    name = None
    page = 1
    while True:
        data = graphql(
            CHARACTER_REPORTS_QUERY,
            {**character, "boss": boss, "page": page, "limit": REPORTS_PAGE_SIZE},
        )["characterData"]["character"]
        if data is None:
            raise ValueError(f"Character not found on Warcraft Logs: {character}")
        name = data["name"]
        reports = data["recentReports"]
        codes += [r["code"] for r in reports["data"] if r["fights"]]
        if page >= reports["last_page"]:
            return name, codes
        page += 1


def unique_pulls(pulls: list[dict]) -> list[dict]:
    """Collapse copies of one pull logged in several reports.

    Each pull needs "abs_start" (ms since epoch) and "difficulty". Copies are pulls of
    the same difficulty starting within SAME_PULL_WINDOW_MS of each other; the first
    copy in time order is kept and the codes of the others go in "copies".
    """
    kept: list[dict] = []
    for pull in sorted(pulls, key=lambda p: p["abs_start"]):
        # Another raid's pull can land between two copies, so check every kept
        # pull inside the window, not just the last one.
        original = next(
            (
                k for k in reversed(kept)
                if pull["abs_start"] - k["abs_start"] < SAME_PULL_WINDOW_MS
                and k["difficulty"] == pull["difficulty"]
            ),
            None,
        )
        if original is not None:
            original["copies"].append(pull["code"])
        else:
            kept.append({**pull, "copies": []})
    return kept


def encounter_pulls(codes: list[str], boss: int, cache_dir: str) -> list[dict]:
    """Unique pulls of the boss across the reports, with difficulty and outcome."""
    pulls = []
    for code in codes:
        report = fetch_report_fights(code, cache_dir)
        for fight in report["fights"] or []:
            if fight["encounterID"] != boss:
                continue
            pulls.append({
                "code": code,
                "fight_id": fight["id"],
                "encounter": fight["name"],
                "difficulty": fight["difficulty"],
                "kill": bool(fight["kill"]),
                "abs_start": report["startTime"] + fight["startTime"],
            })
    return unique_pulls(pulls)


def difficulty_summary(pulls: list[dict]) -> dict[int, dict]:
    """difficulty -> {"pulls", "kills"}, for asking which difficulty to analyse."""
    summary: dict[int, dict] = {}
    for pull in pulls:
        entry = summary.setdefault(pull["difficulty"], {"pulls": 0, "kills": 0})
        entry["pulls"] += 1
        entry["kills"] += pull["kill"]
    return summary


def pulls_with_player(pulls: list[dict], player: str, cache_dir: str) -> list[dict]:
    """Pulls where `player` had a role (tank/healer/DPS) in playerDetails."""
    kept = []
    for pull in pulls:
        details = fetch_player_details(pull["code"], pull["fight_id"], cache_dir)
        names = {e["name"] for bucket in ("tanks", "healers", "dps") for e in details.get(bucket) or []}
        if player in names:
            kept.append(pull)
    return kept


def _remap_entries(entries: list[dict], id_map: dict[int, int], next_id: list[int]) -> list[dict]:
    remapped = []
    for entry in entries:
        if entry["id"] not in id_map:
            id_map[entry["id"]] = next_id[0]
            next_id[0] += 1
        remapped.append({**entry, "id": id_map[entry["id"]]})
    return remapped


def merge_pulls(pulls: list[dict], cache_dir: str, wipe_cutoff: int) -> dict:
    """Fetch the pulls and merge them into one report dict (see fetch.fetch_report).

    Adds "sources": one entry per report with its code, pulls and kills, in the
    order the reports were first pulled.
    """
    merged = {
        "code": None, "title": None, "zone": None, "fights": [], "player_actors": {},
        "pet_owner": {}, "player_details_by_fight": {}, "tables": {}, "sources": [],
    }
    player_ids: dict[tuple[str, str], int] = {}
    next_other_id = [FIRST_NON_PLAYER_ID]

    by_code: dict[str, list[dict]] = {}
    for pull in sorted(pulls, key=lambda p: p["abs_start"]):
        by_code.setdefault(pull["code"], []).append(pull)

    for code, code_pulls in by_code.items():
        report = fetch_report(
            code, cache_dir=cache_dir, wipe_cutoff=wipe_cutoff,
            fight_ids={p["fight_id"] for p in code_pulls},
        )
        merged["zone"] = merged["zone"] or report.get("zone")
        id_map: dict[int, int] = {}
        for local_id, actor in report["player_actors"].items():
            global_id = player_ids.setdefault((actor["name"], actor["server"]), len(player_ids) + 1)
            id_map[local_id] = global_id
            known = merged["player_actors"].get(global_id)
            if known is None or known["class"] == "Unknown":
                merged["player_actors"][global_id] = dict(actor)

        for fight in report["fights"]:
            new_id = len(merged["fights"]) + 1
            merged["fights"].append({**fight, "id": new_id, "source": f"{code}#{fight['id']}"})
            details = report["player_details_by_fight"][fight["id"]]
            merged["player_details_by_fight"][new_id] = {
                bucket: _remap_entries(details.get(bucket) or [], id_map, next_other_id)
                for bucket in ("tanks", "healers", "dps")
            }
            merged["tables"][new_id] = {
                data_type: {**table, "entries": _remap_entries(table.get("entries", []), id_map, next_other_id)}
                for data_type, table in report["tables"][fight["id"]].items()
            }

        for pet_id, owner_id in report["pet_owner"].items():
            if pet_id in id_map and owner_id in id_map:
                merged["pet_owner"][id_map[pet_id]] = id_map[owner_id]

        merged["sources"].append({
            "code": code,
            "date": datetime.fromtimestamp(code_pulls[0]["abs_start"] / 1000),
            "pulls": len(code_pulls),
            "kills": sum(p["kill"] for p in code_pulls),
        })

    return merged
