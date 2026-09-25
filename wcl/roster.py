"""Core roster file (team_roster.yaml): limit the ranked players to the team's mains.

Filtering happens after metrics are built, so each pull's output normalisation still
compares a player against everyone who was actually in that pull.
"""
from __future__ import annotations

import yaml

# Roster groups -> the role names used in metrics rows.
ROSTER_ROLES = {"tank": "tank", "heal": "healer", "melee": "dps", "ranged": "dps"}


def load_roster(path: str) -> dict[str, dict]:
    """name -> {"role", "class"} for every main in the roster file."""
    with open(path, "r", encoding="utf-8") as f:
        groups = yaml.safe_load(f)["main_roster"]
    return {
        member["name"]: {"role": ROSTER_ROLES[group], "class": member["class"]}
        for group, members in groups.items()
        for member in members or []
    }


def filter_to_roster(rows: list[dict], roster: dict[str, dict]) -> dict:
    """Keep only roster mains. Returns {"rows", "dropped", "missing", "mismatches"}:
    players not on the roster, mains with no pulls, and mains whose role or class in
    the logs differs from the roster file."""
    kept, dropped, mismatches = [], [], []
    for row in rows:
        main = roster.get(row["name"])
        if main is None:
            dropped.append(row["name"])
            continue
        if row["role"] != main["role"] or row["class"].replace(" ", "") != main["class"].replace(" ", ""):
            mismatches.append(
                f"{row['name']}: logs say {row['role']} {row['class']}, roster says {main['role']} {main['class']}"
            )
        kept.append(row)
    seen = {row["name"] for row in rows}
    return {
        "rows": kept,
        "dropped": sorted(dropped),
        "missing": sorted(name for name in roster if name not in seen),
        "mismatches": mismatches,
    }
