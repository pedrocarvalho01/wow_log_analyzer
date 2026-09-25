"""Per-player, per-fight metrics built from a fetched report (see fetch.py).

Public entry point: build_player_metrics(report, wipe_cutoff).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict

LOW_SAMPLE_FRACTION_DEFAULT = 0.70   # below this: marked as a low sample
MIN_SAMPLE_FRACTION_DEFAULT = 0.30   # below this: not ranked and never cut
EARLY_DEATH_FRACTION = 0.50


def _index_table_entries(table: dict) -> dict[int, dict]:
    return {e["id"]: e for e in table.get("entries", [])}


def _role_spec_ilvl_by_fight(report: dict) -> dict[int, dict[int, dict]]:
    """fight_id -> player_id -> {"role", "spec", "ilvl"}."""
    out: dict[int, dict[int, dict]] = {}
    for fid, details in report["player_details_by_fight"].items():
        by_id: dict[int, dict] = {}
        for role_key, role_name in (
            ("tanks", "tank"),
            ("healers", "healer"),
            ("dps", "dps"),
        ):
            for entry in details.get(role_key, []) or []:
                specs = entry.get("specs") or []
                spec = specs[0]["spec"] if specs else None
                ilvl = entry.get("maxItemLevel") or entry.get("minItemLevel")
                by_id[entry["id"]] = {"role": role_name, "spec": spec, "ilvl": ilvl}
        out[fid] = by_id
    return out


def _resolve_players(report: dict) -> dict[int, dict]:
    """player_id -> {"name", "class", "role", "spec", "ilvl"} using majority
    role/spec across attended pulls; ilvl is the mean of per-pull values."""
    role_spec_by_fight = _role_spec_ilvl_by_fight(report)

    roles: dict[int, Counter] = defaultdict(Counter)
    specs: dict[int, Counter] = defaultdict(Counter)
    ilvls: dict[int, list[float]] = defaultdict(list)

    for fid, by_id in role_spec_by_fight.items():
        for pid, info in by_id.items():
            roles[pid][info["role"]] += 1
            if info["spec"]:
                specs[pid][info["spec"]] += 1
            if info["ilvl"]:
                ilvls[pid].append(info["ilvl"])

    players = {}
    for pid, actor in report["player_actors"].items():
        if pid not in roles:
            continue  # never appears with a role (e.g. a disconnected/observer actor)
        role = roles[pid].most_common(1)[0][0]
        spec = specs[pid].most_common(1)[0][0] if specs[pid] else None
        ilvl = round(statistics.mean(ilvls[pid]), 1) if ilvls[pid] else None
        players[pid] = {
            "name": actor["name"],
            "class": actor["class"],
            "role": role,
            "spec": spec,
            "ilvl": ilvl,
        }
    return players


def _merge_pet_total(table_entries: dict[int, dict], pet_owner: dict[int, int], owner_id: int) -> float:
    """Sum of a table's `total` for any pet whose petOwner resolves to owner_id."""
    total = 0.0
    for pet_id, owner in pet_owner.items():
        if owner == owner_id and pet_id in table_entries:
            total += table_entries[pet_id].get("total", 0)
    return total


def _first_death_timestamp(deaths_entries: list[dict], player_id: int) -> float | None:
    timestamps = [e["timestamp"] for e in deaths_entries if e["id"] == player_id]
    return min(timestamps) if timestamps else None


def _count_deaths(deaths_entries: list[dict], player_id: int) -> int:
    return sum(1 for e in deaths_entries if e["id"] == player_id)


def _count_early_deaths(
    deaths_entries: list[dict], player_id: int, fight_start: float, duration: float
) -> int:
    count = 0
    for e in deaths_entries:
        if e["id"] != player_id:
            continue
        elapsed = e["timestamp"] - fight_start
        if duration > 0 and elapsed <= duration * EARLY_DEATH_FRACTION:
            count += 1
    return count


def survival_pct_for_pull(
    deaths_entries: list[dict], player_id: int, fight_start: float, duration: float
) -> float:
    """100 if the player did not die in this pull, else the % of the pull
    they survived before their first death."""
    first_death = _first_death_timestamp(deaths_entries, player_id)
    if first_death is None:
        return 100.0
    if duration <= 0:
        return 0.0
    elapsed = first_death - fight_start
    return max(0.0, min(100.0, (elapsed / duration) * 100.0))


def normalise_output(player_output: float, role_outputs_this_pull: list[float]) -> float | None:
    """output_norm = player_output / median(role output in that pull).

    Returns None if there's no valid role median (e.g. sole player of that
    role, or median is zero) rather than dividing by zero.
    """
    if not role_outputs_this_pull:
        return None
    median = statistics.median(role_outputs_this_pull)
    if median <= 0:
        return None
    return player_output / median


def _analysed_window(tables: dict, fight: dict) -> float:
    """Milliseconds of the pull the tables cover: `totalTime` of the damage
    table (truncated at the wipe cutoff), else the full pull length."""
    for data_type in ("DamageDone", "Healing", "DamageTaken"):
        total_time = (tables.get(data_type) or {}).get("totalTime")
        if total_time:
            return total_time
    return fight["endTime"] - fight["startTime"]


def _share_pct(part: float, whole: float) -> float | None:
    return part / whole * 100 if whole > 0 else None


def duration_weighted_average(values_and_weights: list[tuple[float, float]]) -> float | None:
    values_and_weights = [(v, w) for v, w in values_and_weights if v is not None and w > 0]
    total_weight = sum(w for _, w in values_and_weights)
    if total_weight <= 0:
        return None
    return sum(v * w for v, w in values_and_weights) / total_weight


def build_player_metrics(report: dict, config: dict) -> list[dict]:
    """Return one metrics row per player (unsorted)."""
    players = _resolve_players(report)
    fights = report["fights"]
    pet_owner = report["pet_owner"]
    attribution_specs = set(config.get("attribution_specs", []))
    roster_cfg = config.get("roster", {})
    low_sample_fraction = roster_cfg.get("low_sample_pulls_fraction", LOW_SAMPLE_FRACTION_DEFAULT)
    min_sample_fraction = roster_cfg.get("min_sample_pulls_fraction", MIN_SAMPLE_FRACTION_DEFAULT)
    total_pulls = len(fights)

    # Per-player, per-pull raw records, built once per fight.
    pull_records: dict[int, list[dict]] = defaultdict(list)

    for fight in fights:
        fid = fight["id"]
        tables = report["tables"][fid]
        # The tables are truncated at the wipe cutoff, so rates and active time
        # use the table's analysed window (totalTime), not the full pull length.
        duration = _analysed_window(tables, fight)
        pull_length = fight["endTime"] - fight["startTime"]
        dmg_entries = _index_table_entries(tables["DamageDone"])
        heal_entries = _index_table_entries(tables["Healing"])
        taken_entries = _index_table_entries(tables["DamageTaken"])
        deaths_entries = tables["Deaths"].get("entries", [])

        # Attendance: a player attended this pull if they appear in any table.
        attended_ids = set(dmg_entries) | set(heal_entries) | set(taken_entries)
        attended_ids &= set(players)  # exclude NPCs/pets accidentally matched

        # Role output pools for this pull, for output_norm's median.
        role_output_pool: dict[str, list[float]] = defaultdict(list)
        pull_output_by_player: dict[int, float] = {}
        pull_active_pct_by_player: dict[int, float] = {}

        for pid in attended_ids:
            role = players[pid]["role"]
            if role == "healer":
                # A player present in the pull but missing from the output table
                # (e.g. an instant wipe) did 0 output, not "no data".
                entry = heal_entries.get(pid, {})
                # Healing `total` is already effective healing; overheal is reported separately.
                net = entry.get("total", 0) + _merge_pet_total(heal_entries, pet_owner, pid)
                rate = net / (duration / 1000) if duration > 0 else 0.0
                active_pct = (entry.get("activeTime", 0) / duration * 100) if duration > 0 else 0.0
            else:
                entry = dmg_entries.get(pid, {})
                total = entry.get("total", 0) + _merge_pet_total(dmg_entries, pet_owner, pid)
                rate = total / (duration / 1000) if duration > 0 else 0.0
                active_pct = (entry.get("activeTime", 0) / duration * 100) if duration > 0 else 0.0

            pull_output_by_player[pid] = rate
            pull_active_pct_by_player[pid] = active_pct
            role_output_pool[role].append(rate)

        for pid in attended_ids:
            role = players[pid]["role"]
            rate = pull_output_by_player.get(pid)
            if rate is None:
                continue
            taken_entry = taken_entries.get(pid, {})
            dtaken_total = taken_entry.get("total", 0)
            # In the DamageTaken table `overheal` holds the damage absorbed before it landed.
            dtaken_absorbed = taken_entry.get("overheal", 0) or 0
            heal_entry = heal_entries.get(pid, {})

            pull_records[pid].append(
                {
                    "fight_id": fid,
                    "duration": duration,
                    "output": rate,
                    "output_norm": normalise_output(rate, role_output_pool[role]),
                    "active_pct": pull_active_pct_by_player.get(pid, 0.0),
                    "survival_pct": survival_pct_for_pull(
                        deaths_entries, pid, fight["startTime"], pull_length
                    ),
                    "deaths": _count_deaths(deaths_entries, pid),
                    "early_deaths": _count_early_deaths(
                        deaths_entries, pid, fight["startTime"], pull_length
                    ),
                    "damage_taken": dtaken_total,
                    "damage_taken_rate": dtaken_total / (duration / 1000) if duration > 0 else 0.0,
                    "damage_absorbed": dtaken_absorbed,
                    "heal_effective": heal_entry.get("total", 0) if role == "healer" else 0,
                    "heal_overheal": (heal_entry.get("overheal", 0) or 0) if role == "healer" else 0,
                }
            )

    rows = []
    for pid, info in players.items():
        pulls = pull_records.get(pid, [])
        pulls_attended = len(pulls)
        if pulls_attended == 0:
            continue

        output_rate = duration_weighted_average(
            [(p["output"], p["duration"]) for p in pulls]
        )
        output_norm_avg = duration_weighted_average(
            [(p["output_norm"], p["duration"]) for p in pulls if p["output_norm"] is not None]
        )
        active_pct = duration_weighted_average(
            [(p["active_pct"], p["duration"]) for p in pulls]
        )
        survival_pct = statistics.mean(p["survival_pct"] for p in pulls)
        deaths = sum(p["deaths"] for p in pulls)
        early_deaths = sum(p["early_deaths"] for p in pulls)
        damage_taken_total = sum(p["damage_taken"] for p in pulls)
        damage_taken_rate = duration_weighted_average(
            [(p["damage_taken_rate"], p["duration"]) for p in pulls]
        )
        # Ratios of sums across pulls, matching how the web UI aggregates.
        damage_absorbed = sum(p["damage_absorbed"] for p in pulls)
        mitigated_pct = _share_pct(damage_absorbed, damage_taken_total + damage_absorbed)
        heal_overheal = sum(p["heal_overheal"] for p in pulls)
        heal_gross = sum(p["heal_effective"] for p in pulls) + heal_overheal
        overheal_pct = _share_pct(heal_overheal, heal_gross) if info["role"] == "healer" else None

        attendance = pulls_attended / total_pulls if total_pulls else 0.0
        low_sample = attendance < low_sample_fraction

        # Players the data can't fairly compare are excluded from the cut.
        spec_key = f"{info['class']}:{info['spec']}"
        if spec_key in attribution_specs:
            exclude_reason = "support_spec"
        elif attendance < min_sample_fraction:
            exclude_reason = "low_sample"
        else:
            exclude_reason = None

        rows.append(
            {
                "id": pid,
                "name": info["name"],
                "class": info["class"],
                "spec": info["spec"],
                "role": info["role"],
                "ilvl": info["ilvl"],
                "pulls_attended": pulls_attended,
                "total_pulls": total_pulls,
                "output": output_rate,
                "output_norm": output_norm_avg,
                "active_pct": active_pct,
                "survival_pct": survival_pct,
                "deaths": deaths,
                "early_deaths": early_deaths,
                "damage_taken_total": damage_taken_total,
                "damage_taken_rate": damage_taken_rate,
                "mitigated_pct": mitigated_pct,
                "overheal_pct": overheal_pct,
                "low_sample": low_sample,
                "exclude_reason": exclude_reason,
            }
        )

    return rows
