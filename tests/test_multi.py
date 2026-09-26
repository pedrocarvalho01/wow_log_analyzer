"""Character mode (wcl/multi.py) and the core roster filter (wcl/roster.py)."""
import pytest

import wcl.multi as multi
from wcl.multi import (
    difficulty_summary,
    merge_pulls,
    parse_character_url,
    parse_difficulty,
    unique_pulls,
)
from wcl.roster import filter_to_roster


def _pull(code, fid, start, difficulty=4, kill=False):
    return {"code": code, "fight_id": fid, "difficulty": difficulty, "kill": kill,
            "abs_start": start, "encounter": "Boss"}


def test_parse_character_url_by_id_and_boss():
    assert parse_character_url("https://www.warcraftlogs.com/character/id/86473646?boss=3497") == (
        {"id": 86473646}, 3497)


def test_parse_character_url_by_name_ignores_all_bosses_view():
    lookup, boss = parse_character_url("https://www.warcraftlogs.com/character/eu/silvermoon/falkien?boss=-2")
    assert lookup == {"region": "EU", "server": "silvermoon", "name": "falkien"}
    assert boss is None


def test_parse_difficulty():
    assert parse_difficulty("Heroic") == 4
    assert parse_difficulty("mythic") == 5
    assert parse_difficulty("1") == 1
    with pytest.raises(ValueError):
        parse_difficulty("hard")


def test_unique_pulls_collapses_copies_of_one_pull():
    pulls = [
        _pull("B", 7, 1_000_500),          # same pull as A#3, logged by someone else
        _pull("A", 3, 1_000_000),
        _pull("A", 4, 1_400_000),          # next pull, minutes later
        _pull("C", 1, 1_000_200, difficulty=3),  # same time, other difficulty: another raid
    ]
    kept = unique_pulls(pulls)
    assert [(p["code"], p["fight_id"]) for p in kept] == [("A", 3), ("C", 1), ("A", 4)]
    assert kept[0]["copies"] == ["B"]


def test_difficulty_summary_counts_pulls_and_kills():
    pulls = [_pull("A", 1, 0, 4), _pull("A", 2, 1, 4, kill=True), _pull("B", 1, 2, 3, kill=True)]
    assert difficulty_summary(pulls) == {4: {"pulls": 2, "kills": 1}, 3: {"pulls": 1, "kills": 1}}


def _fake_report(actors, entries):
    """One-fight report where `entries` are (local id, total) rows of every table."""
    table = {"entries": [{"id": i, "total": t} for i, t in entries], "totalTime": 1000}
    return {
        "player_actors": {i: {"name": n, "class": "Mage", "server": "Silvermoon"} for i, n in actors.items()},
        "pet_owner": {},
        "fights": [{"id": 1, "kill": True, "startTime": 0, "endTime": 1000}],
        "player_details_by_fight": {1: {"dps": [{"id": i, "name": n} for i, n in actors.items()]}},
        "tables": {1: {"DamageDone": table}},
    }


def test_merge_pulls_rekeys_players_by_name_across_reports(monkeypatch):
    reports = {
        # Same player "Ana" has id 5 in one report and id 9 in the other; id 9 in A is someone else.
        "A": _fake_report({5: "Ana", 9: "Bo"}, [(5, 100), (9, 50), (77, 1)]),
        "B": _fake_report({9: "Ana"}, [(9, 200), (77, 1)]),
    }
    monkeypatch.setattr(multi, "fetch_report", lambda code, **kw: reports[code])
    merged = merge_pulls([_pull("A", 1, 0, kill=True), _pull("B", 1, 10_000_000, kill=True)], "cache", 3)

    names = {gid: a["name"] for gid, a in merged["player_actors"].items()}
    ana = next(gid for gid, n in names.items() if n == "Ana")
    assert sorted(names.values()) == ["Ana", "Bo"]
    assert [f["id"] for f in merged["fights"]] == [1, 2]
    totals = {fid: {e["id"]: e["total"] for e in t["DamageDone"]["entries"]} for fid, t in merged["tables"].items()}
    assert totals[1][ana] == 100 and totals[2][ana] == 200
    # A non-player actor never takes a player's merged id.
    assert all(i >= multi.FIRST_NON_PLAYER_ID for i in totals[1] if i not in names)
    assert [s["code"] for s in merged["sources"]] == ["A", "B"]


def test_filter_to_roster_drops_guests_and_flags_mismatches():
    roster = {"Ana": {"role": "healer", "class": "Priest"},
              "Bo": {"role": "dps", "class": "Death Knight"},
              "Cy": {"role": "tank", "class": "Monk"}}
    rows = [{"name": "Ana", "role": "dps", "class": "Priest"},
            {"name": "Bo", "role": "dps", "class": "DeathKnight"},
            {"name": "Guest", "role": "dps", "class": "Mage"}]
    result = filter_to_roster(rows, roster)
    assert [r["name"] for r in result["rows"]] == ["Ana", "Bo"]
    assert result["dropped"] == ["Guest"]
    assert result["missing"] == ["Cy"]
    assert len(result["mismatches"]) == 1 and result["mismatches"][0].startswith("Ana:")
