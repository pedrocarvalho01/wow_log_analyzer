"""Per-player facts the text is written from (roster-WRITING-GUIDE.md §2).

Every comparison uses what the reader can see: raw DPS/HPS as shown in the
table, within the role. Protected players are left out of every pool, so no
statement about someone else depends on a protected player's numbers, and
excluded players (support specs, too few pulls) are left out of the output
ranking because their numbers are not comparable.
"""
from __future__ import annotations

import math
import statistics

from wcl.rank import STATUS_NOT_EVALUATED

DEFAULTS = {
    "high_overheal_pct": 45.0,
    "low_overheal_pct": 30.0,
    "ilvl_gap": 5.0,
    "low_active_pct": 90.0,
    "clear_lead_pct": 5.0,
}


def _visible(row: dict) -> bool:
    return row.get("status") != STATUS_NOT_EVALUATED


def _rank_from_bottom(value: float, pool_values: list[float]) -> int:
    """1 = lowest; ties share the better (lower) rank."""
    return 1 + sum(1 for v in pool_values if v < value)


def _rank_from_top(value: float, pool_values: list[float]) -> int:
    return 1 + sum(1 for v in pool_values if v > value)


def compute_signals(rows: list[dict], composition: dict[str, int], config: dict) -> dict[str, dict]:
    """Return {player name: signals} for every visible player."""
    cfg = {**DEFAULTS, **(config.get("writing") or {})}
    visible = [r for r in rows if _visible(r)]

    role_pool = {
        role: [r for r in visible if r["role"] == role and not r.get("exclude_reason")]
        for role in ("tank", "healer", "dps")
    }
    raid_survival = [r["survival_pct"] or 0.0 for r in visible]
    survival_quartile = max(1, math.ceil(len(raid_survival) * 0.25))
    ilvls = [r["ilvl"] for r in visible if r.get("ilvl")]
    raid_ilvl_median = statistics.median(ilvls) if ilvls else None
    top_tank_load = max((r["damage_taken_total"] or 0 for r in visible), default=0)
    class_counts: dict[str, list[dict]] = {}
    for r in visible:
        class_counts.setdefault(r["class"], []).append(r)

    out: dict[str, dict] = {}
    for row in visible:
        pool = role_pool[row["role"]]
        outputs = [r["output"] or 0.0 for r in pool]
        output = row["output"] or 0.0
        in_pool = row in pool
        sig: dict = {"role": row["role"], "n_in_role": len(pool)}

        if in_pool and outputs:
            median = statistics.median(outputs)
            sig["output_rank_top"] = _rank_from_top(output, outputs)
            sig["output_rank_bottom"] = _rank_from_bottom(output, outputs)
            sig["output_vs_median"] = output / median - 1 if median else 0.0
            ranked = sorted(outputs, reverse=True)
            if sig["output_rank_top"] == 1 and len(ranked) > 1 and ranked[1] > 0:
                sig["lead_margin"] = output / ranked[1] - 1
            sig["clear_lead"] = sig.get("lead_margin", 0.0) * 100 > cfg["clear_lead_pct"]

        survival = row["survival_pct"] or 0.0
        sig["deaths"] = row.get("deaths", 0)
        sig["no_deaths"] = sig["deaths"] == 0
        if not sig["no_deaths"]:
            rank = _rank_from_bottom(survival, raid_survival)
            sig["survival_rank_bottom"] = rank
            sig["survival_bottom_quartile"] = rank <= survival_quartile
        sig["survival_above_role_median"] = survival >= statistics.median(
            [r["survival_pct"] or 0.0 for r in pool] or [survival]
        )

        active = row["active_pct"] or 0.0
        pool_active = [r["active_pct"] or 0.0 for r in pool]
        sig["lowest_active"] = (
            in_pool and active < cfg["low_active_pct"] and active <= min(pool_active)
        )

        if row.get("ilvl") and raid_ilvl_median is not None:
            sig["ilvl"] = row["ilvl"]
            sig["ilvl_low"] = row["ilvl"] <= raid_ilvl_median - cfg["ilvl_gap"]
            sig["ilvl_lowest"] = row["ilvl"] <= min(ilvls)

        taken = row["damage_taken_total"] or 0
        sig["damage_taken_m"] = round(taken / 1e6)
        if in_pool and len(pool) > 1:
            pool_taken = [r["damage_taken_total"] or 0 for r in pool]
            pool_mitigation = [r["mitigated_pct"] or 0.0 for r in pool]
            sig["dtaken_high"] = bool(row.get("high_damage_taken"))
            sig["dtaken_highest"] = taken >= max(pool_taken)
            sig["dtaken_low"] = _rank_from_bottom(taken, pool_taken) <= max(1, len(pool) // 4)
            sig["mitigation_low"] = (row["mitigated_pct"] or 0.0) <= sorted(pool_mitigation)[
                max(0, len(pool) // 4 - 1)
            ]
            sig["mitigated_pct"] = row["mitigated_pct"]

        if row["role"] == "healer" and row.get("overheal_pct") is not None:
            sig["overheal_pct"] = row["overheal_pct"]
            sig["overheal_high"] = row["overheal_pct"] > cfg["high_overheal_pct"]
            sig["overheal_low"] = row["overheal_pct"] < cfg["low_overheal_pct"]

        slots = composition.get(row["role"], 0)
        if in_pool and len(pool) > slots and row["role"] in ("healer", "tank"):
            sig["slot_position"] = sig["output_rank_top"]

        same_class = class_counts[row["class"]]
        if len(same_class) >= 3:
            weakest = min(same_class, key=lambda r: r["output"] or 0.0)
            if weakest is row:
                sig["class_position"] = len(same_class)

        if row.get("low_sample"):
            sig["sample"] = (row["pulls_attended"], row["total_pulls"])

        if row["role"] == "tank":
            sig["tank_load_top"] = taken >= top_tank_load and taken > 0
            sig["mitigated_pct"] = row["mitigated_pct"]

        out[row["name"]] = sig
    return out
