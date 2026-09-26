"""Role-relative ranking and roster cut proposal.

Implements roster-DECISION-GUIDE.md (which supersedes BUILD.md §5-6 on how
cuts are decided): throughput decides the order within a role, and survival,
active time, damage taken and overhealing only decide close cases.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter

STATUS_REMOVE = "Remove"
STATUS_RESERVE = "Reserve"
STATUS_KEEP = "Keep"
STATUS_ESSENTIAL = "Essential"
STATUS_RAID_LEADER = "Raid Leader"
STATUS_FIXED = "Fixed"
# Fixed players (the raid leader, or anyone the raid leader pins) are never cut
# and don't move the cut line, but their numbers and rationale are still shown.
FIXED_STATUSES = (STATUS_RAID_LEADER, STATUS_FIXED)

CONFIDENCE_CLEAR = "clear"
CONFIDENCE_SUPPORTED = "supported"
CONFIDENCE_CLOSE_CALL = "close call"

EXCLUDE_SUPPORT_SPEC = "support_spec"
EXCLUDE_LOW_SAMPLE = "low_sample"

ROLES = ("tank", "healer", "dps")


def display_class_name(class_name: str) -> str:
    """'DeathKnight' -> 'Death Knight'; spaces the PascalCase class id for display."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", class_name)


def _class_key(class_name: str) -> str:
    """Config may say 'Demon Hunter' where the API says 'DemonHunter'."""
    return class_name.replace(" ", "")


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _zscores(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0 for _ in values]
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return [0.0 for _ in values]
    return [(v - mean) / stdev for v in values]


def _is_excluded(row: dict) -> bool:
    return bool(row.get("exclude_reason"))


# --- Scoring --------------------------------------------------------------


def _flag_damage_taken(rows: list[dict]) -> None:
    """Attach `dtaken_z` (clipped to [-2, 2]) and `high_damage_taken`
    (> role mean + 1 SD). These are flags for notes, not score inputs."""
    rates = [r["damage_taken_rate"] or 0.0 for r in rows]
    mean = statistics.mean(rates) if rates else 0.0
    stdev = statistics.pstdev(rates) if len(rates) > 1 else 0.0
    for row, z in zip(rows, _zscores(rates)):
        row["dtaken_z"] = _clip(z, -2.0, 2.0)
        row["high_damage_taken"] = stdev > 0 and (row["damage_taken_rate"] or 0.0) > mean + stdev


def _score_tanks(rows: list[dict], weights: dict) -> None:
    """Tanks are judged on survival, mitigation and their share of the damage
    the tanks took; their own DPS does not count."""
    max_taken = max((r["damage_taken_total"] or 0 for r in rows), default=0)
    for row in rows:
        share = (row["damage_taken_total"] or 0) / max_taken if max_taken else 0.0
        row["score"] = (
            weights["survival"] * (row["survival_pct"] or 0.0) / 100.0
            + weights["mitigation"] * (row["mitigated_pct"] or 0.0) / 100.0
            + weights["damage_taken_share"] * share
        )


def score_players(rows: list[dict], config: dict) -> list[dict]:
    """Return rows (copies) with an in-role `score`. For DPS and healers the
    score is the normalised output, because throughput decides the order;
    players excluded from the cut get no score."""
    rows = [dict(r) for r in rows]
    by_role = {role: [r for r in rows if r["role"] == role] for role in ROLES}

    for role in ("healer", "dps"):
        scorable = [r for r in by_role[role] if not _is_excluded(r)]
        for row in scorable:
            row["score"] = row["output_norm"] if row["output_norm"] is not None else 0.0
        _flag_damage_taken(scorable)
    _score_tanks(by_role["tank"], config["scoring"]["tank"])
    _flag_damage_taken(by_role["tank"])

    for row in rows:
        row.setdefault("score", None)
        row.setdefault("dtaken_z", 0.0)
        row.setdefault("high_damage_taken", False)
    return rows


# --- Overall order --------------------------------------------------------


def _role_sort_key(row: dict) -> tuple:
    """Lowest first; equal scores are separated by survival."""
    return (row.get("score") or 0.0, row.get("survival_pct") or 0.0)


def _assign_role_percentiles(rows: list[dict]) -> None:
    """0..1 percentile within the role (0 = worst). Protected players are
    ranked by their numbers too; excluded players are not ranked."""
    for role in ROLES:
        ranked = sorted(
            (r for r in rows if r["role"] == role and not _is_excluded(r)), key=_role_sort_key
        )
        n = len(ranked)
        for i, row in enumerate(ranked):
            row["role_percentile"] = i / (n - 1) if n > 1 else 0.5


def _order_rows(rows: list[dict]) -> list[dict]:
    """Order worst -> best and number the rows (#1 = worst).

    Protected players sit where their numbers put them. Excluded players are
    not comparable, so they follow the ranked pool, and tanks filling their
    slots go at the best end, just below the single top performer."""
    essential = [r for r in rows if r.get("essential")]
    excluded = [r for r in rows if _is_excluded(r) and not r.get("essential")]
    pool = [r for r in rows if r not in essential and r not in excluded]
    pool.sort(key=lambda r: (r.get("role_percentile", 0.5), r.get("survival_pct") or 0.0))
    essential.sort(key=lambda r: r.get("role_percentile", 0.5))

    top = pool[-1:] if pool else []
    ordered = pool[:-1] + excluded + essential + top if pool else excluded + essential
    for i, row in enumerate(ordered, start=1):
        row["rank"] = i
    return ordered


def build_overall_order(
    rows: list[dict], protected_names: set[str], composition: dict[str, int]
) -> list[dict]:
    """Percentile-rank every player within their role, mark the tanks that fill
    the tank slots as essential, and order everyone worst -> best."""
    _assign_role_percentiles(rows)

    tanks = sorted(
        (r for r in rows if r["role"] == "tank" and r["name"] not in protected_names),
        key=lambda r: r.get("role_percentile", 0.5),
    )
    excess = max(0, len(tanks) - composition.get("tank", 0))
    for i, row in enumerate(tanks):
        row["essential"] = i >= excess
    return _order_rows(rows)


# --- Cut decisions --------------------------------------------------------


def _gap_pct(a: dict, b: dict) -> float:
    """Output gap between two players, as % of the higher one."""
    a_norm = a["output_norm"] or 0.0
    b_norm = b["output_norm"] or 0.0
    denom = max(abs(a_norm), abs(b_norm), 1e-9)
    return abs(a_norm - b_norm) / denom * 100


def _near_tie(a: dict, b: dict, cut_rules: dict) -> bool:
    return _gap_pct(a, b) <= cut_rules["near_tie_output_pct"]


def _class_redundancy_pick(candidates: list[dict], kept_classes: Counter) -> dict:
    """Among near-tied candidates, cut whichever class is most redundant
    (highest count already kept on the roster)."""
    return max(candidates, key=lambda r: kept_classes[r["class"]])


def _survival(row: dict) -> float:
    return row["survival_pct"] or 0.0


def _secondaries_favour(kept: dict, cut: dict) -> bool:
    """True if every secondary metric favours the kept player over the cut one."""
    checks = [
        _survival(kept) >= _survival(cut),
        (kept["active_pct"] or 0.0) >= (cut["active_pct"] or 0.0),
    ]
    if cut["role"] == "healer":
        checks.append((kept["overheal_pct"] or 0.0) <= (cut["overheal_pct"] or 0.0))
    else:
        checks.append((kept["damage_taken_rate"] or 0.0) <= (cut["damage_taken_rate"] or 0.0))
    return all(checks)


def _resolve_near_tie(cut_edge: dict, keep_edge: dict, cut_rules: dict, kept_classes: Counter) -> dict:
    """Decision-guide §4a for two players under the near-tie threshold.
    Returns the player to cut and records how the tie was broken."""
    survival_gap = abs(_survival(cut_edge) - _survival(keep_edge))
    by_class = _class_redundancy_pick([cut_edge, keep_edge], kept_classes)
    classes_differ = kept_classes[cut_edge["class"]] != kept_classes[keep_edge["class"]]

    if survival_gap >= cut_rules["near_tie_survival_pp"]:
        worse = cut_edge if _survival(cut_edge) < _survival(keep_edge) else keep_edge
        basis = "survival"
        # Survival wins, but the raid leader should know redundancy disagreed.
        conflict = classes_differ and by_class is not worse
    elif classes_differ:
        worse, basis, conflict = by_class, "class redundancy", False
    else:
        worse, basis, conflict = cut_edge, "undecided", False

    better = keep_edge if worse is cut_edge else cut_edge
    worse["confidence"] = CONFIDENCE_CLOSE_CALL
    for row, peer in ((worse, better), (better, worse)):
        row["near_tie_peer"] = peer["name"]
        row["tie_basis"] = basis
        row["tie_conflict"] = conflict
    return worse


def _classify_confidence(cut: dict, floor: dict | None, cut_rules: dict) -> str:
    """Decision-guide §7: how firmly the data supports this cut, measured
    against the lowest player in the role who is not being cut."""
    if floor is None:
        return CONFIDENCE_CLEAR
    gap = _gap_pct(cut, floor)
    if (cut["output_norm"] or 0.0) > (floor["output_norm"] or 0.0) or gap < cut_rules["near_tie_output_pct"]:
        return CONFIDENCE_CLOSE_CALL
    if gap > cut_rules["borderline_output_pct"]:
        return CONFIDENCE_CLEAR
    survival_clear = _survival(floor) - _survival(cut) >= cut_rules["borderline_survival_pp"]
    if survival_clear or _secondaries_favour(floor, cut):
        return CONFIDENCE_SUPPORTED
    return CONFIDENCE_CLOSE_CALL


def _coverage_checks(config: dict) -> list[tuple[str, set[str], int]]:
    """(label, provider classes, minimum kept) for each composition requirement."""
    checks = [
        (buff, {_class_key(c) for c in classes}, 1)
        for buff, classes in config.get("raid_buffs", {}).items()
    ]
    cut_rules = config["cut_rules"]
    if config.get("bloodlust_classes"):
        checks.append((
            "Bloodlust",
            {_class_key(c) for c in config["bloodlust_classes"]},
            cut_rules.get("min_bloodlust_sources", 2),
        ))
    if config.get("battle_res_classes"):
        checks.append((
            "battle res",
            {_class_key(c) for c in config["battle_res_classes"]},
            cut_rules.get("min_battle_res_sources", 2),
        ))
    return checks


def _apply_coverage_swaps(rows: list[dict], by_role: dict[str, list[dict]], config: dict) -> list[str]:
    """Decision-guide §4b: if the cuts leave a buff or utility short, keep the
    best removed provider and cut the lowest kept player in that role instead,
    as long as that doesn't break another requirement."""
    notes: list[str] = []
    checks = _coverage_checks(config)

    def kept() -> list[dict]:
        return [r for r in rows if r.get("status") != STATUS_REMOVE]

    def providers(classes: set[str], pool: list[dict]) -> int:
        return sum(1 for r in pool if _class_key(r["class"]) in classes)

    def still_covered(pool: list[dict]) -> bool:
        return all(providers(classes, pool) >= minimum for _, classes, minimum in checks)

    for _ in range(len(checks) + 1):
        changed = False
        for label, classes, minimum in checks:
            if providers(classes, kept()) >= minimum:
                continue
            removed = sorted(
                (r for r in rows if r.get("status") == STATUS_REMOVE and _class_key(r["class"]) in classes),
                key=lambda r: -r.get("role_percentile", 0.5),
            )
            if not removed:
                count = providers(classes, kept())
                notes.append(
                    f"The roster keeps only {count} {label} {'source' if count == 1 else 'sources'} "
                    f"against the {minimum} required, so coverage should be planned before the next raid."
                )
                continue
            restore = removed[0]
            swap_out = None
            for candidate in sorted(
                (r for r in by_role[restore["role"]] if r["status"] == STATUS_KEEP and not r.get("kept_for")),
                key=lambda r: r.get("role_percentile", 0.5),
            ):
                trial = [r for r in kept() if r is not candidate] + [restore]
                if providers(classes, trial) > providers(classes, kept()) and _other_checks_hold(
                    trial, kept(), checks, label, providers
                ):
                    swap_out = candidate
                    break
            if swap_out is None:
                notes.append(
                    f"Cutting {restore['name']} leaves the raid short of {label}, and no swap within the role "
                    "could fix it; the raid leader should decide how to cover it."
                )
                continue
            restore["status"] = STATUS_KEEP
            restore["kept_for"] = label
            restore["confidence"] = None
            swap_out["status"] = STATUS_REMOVE
            swap_out["cut_for_composition"] = label
            notes.append(f"{restore['name']} kept instead of {swap_out['name']} to preserve {label} coverage")
            changed = True
        if not changed:
            break
    return notes


def _other_checks_hold(trial, current, checks, label, providers) -> bool:
    """A swap must not break any requirement that currently holds."""
    for other_label, classes, minimum in checks:
        if other_label == label:
            continue
        if providers(classes, current) >= minimum and providers(classes, trial) < minimum:
            return False
    return True


def check_decisions(rows: list[dict]) -> list[str]:
    """Decision-guide §8/§11 invariants. Returns problems; empty means OK."""
    problems = []
    for row in rows:
        if row.get("status") == STATUS_REMOVE and (row.get("protected") or _is_excluded(row)):
            problems.append(f"{row['name']} is protected or excluded but was cut")
    for role in ROLES:
        removed = [r for r in rows if r["role"] == role and r["status"] == STATUS_REMOVE
                   and not r.get("cut_for_composition")]
        kept = [r for r in rows if r["role"] == role and r["status"] in (STATUS_KEEP, STATUS_RESERVE)
                and not _is_excluded(r) and not r.get("kept_for")]
        for cut in removed:
            for keep in kept:
                if cut.get("role_percentile", 0) > keep.get("role_percentile", 0):
                    problems.append(f"{cut['name']} (Remove) ranks above {keep['name']} ({keep['status']})")
    return problems


def propose_cuts(
    rows: list[dict],
    target: int,
    composition: dict[str, int],
    protected_names: set[str],
    config: dict,
    raid_leaders: set[str] = frozenset(),
) -> dict:
    """Return {"rows": [...ordered, with status/confidence/rationale...], "notes": [...]}.

    `rows` should already have `score`/`role_percentile` from score_players +
    build_overall_order. `protected_names` are fixed in the roster; those also
    in `raid_leaders` get the Raid Leader status instead of Fixed.
    """
    cut_rules = config["cut_rules"]
    notes: list[str] = []

    for row in rows:
        row["status"] = None
        row["rationale"] = ""
        row["protected"] = row["name"] in protected_names
        for key in ("confidence", "near_tie_peer", "tie_basis", "kept_for", "cut_for_composition"):
            row[key] = None
        row["tie_conflict"] = False

    for row in rows:
        if row["protected"]:
            row["status"] = STATUS_RAID_LEADER if row["name"] in raid_leaders else STATUS_FIXED
        elif _is_excluded(row):
            row["status"] = STATUS_KEEP

    # Protected and excluded players are never cut, but they still fill a slot
    # in their role, so they count as present when sizing the cuts.
    present = Counter(r["role"] for r in rows)
    for role in ROLES:
        if present[role] < composition.get(role, 0):
            notes.append(
                f"Only {present[role]} of {composition[role]} {role} slots can be filled from this raid, "
                "so the role is short and should be recruited or covered before the next raid."
            )

    by_role = {
        role: sorted(
            (r for r in rows if r["role"] == role and r["status"] is None),
            key=lambda r: r.get("role_percentile", 0.5),
        )
        for role in ROLES
    }
    for row in by_role["tank"]:
        if row.get("essential"):
            row["status"] = STATUS_ESSENTIAL

    to_cut = {role: max(0, present[role] - composition.get(role, 0)) for role in ROLES}
    cuttable = {role: [r for r in by_role[role] if r["status"] is None] for role in ROLES}

    # If a role has more uncuttable players than slots, move the excess cuts to
    # other roles (DPS first) so the total still reaches the target.
    for role in ROLES:
        excess = to_cut[role] - len(cuttable[role])
        if excess <= 0:
            continue
        to_cut[role] = len(cuttable[role])
        for other in ("dps", "healer", "tank"):
            if other == role or excess <= 0:
                continue
            moved = min(len(cuttable[other]) - to_cut[other], excess)
            if moved > 0:
                to_cut[other] += moved
                excess -= moved
                notes.append(
                    f"The {role} slots are filled by players who cannot be cut, so {moved} extra "
                    f"{'DPS' if other == 'dps' else other} {'cut was' if moved == 1 else 'cuts were'} made "
                    f"to keep the roster at {target}."
                )

    # Provisional cuts: the lowest `to_cut` in each role.
    for role in ROLES:
        for row in cuttable[role][: to_cut[role]]:
            row["status"] = STATUS_REMOVE

    # Near-tie test at each role's cut line (§4a). Lower cuts are further from
    # the line, so only the boundary pair can be a tie.
    for role in ROLES:
        pool, n_cut = cuttable[role], to_cut[role]
        if n_cut <= 0 or n_cut >= len(pool):
            continue
        cut_edge, keep_edge = pool[n_cut - 1], pool[n_cut]
        if not _near_tie(cut_edge, keep_edge, cut_rules):
            continue
        # Redundancy counts the roster without the other cuts, but with both tied players.
        kept_classes = Counter(
            r["class"] for r in rows if r["status"] != STATUS_REMOVE or r is cut_edge
        )
        worse = _resolve_near_tie(cut_edge, keep_edge, cut_rules, kept_classes)
        if worse is keep_edge:
            cut_edge["status"], keep_edge["status"] = None, STATUS_REMOVE
            # The data can't separate them, so the decision orders them.
            cut_edge["role_percentile"], keep_edge["role_percentile"] = (
                keep_edge["role_percentile"], cut_edge["role_percentile"],
            )
            pool[n_cut - 1], pool[n_cut] = keep_edge, cut_edge

    for role in ROLES:
        for row in by_role[role]:
            if row["status"] is None:
                row["status"] = STATUS_KEEP

    notes.extend(_apply_coverage_swaps(rows, by_role, config))

    # Confidence for each cut against the lowest player kept on merit (§7).
    for role in ROLES:
        kept_on_merit = sorted(
            (r for r in by_role[role] if r["status"] == STATUS_KEEP and not r.get("kept_for")),
            key=lambda r: r.get("role_percentile", 0.5),
        )
        floor = kept_on_merit[0] if kept_on_merit else None
        for row in by_role[role]:
            if row["status"] != STATUS_REMOVE or row["confidence"]:
                continue
            row["confidence"] = _classify_confidence(row, floor, cut_rules)
            if row["confidence"] == CONFIDENCE_CLOSE_CALL and floor and not row["near_tie_peer"]:
                row["near_tie_peer"] = floor["name"]
                floor["near_tie_peer"] = row["name"]

    reserve_count = cut_rules.get("reserve_count", 2)
    for role in ROLES:
        if to_cut[role] <= 0:
            continue
        line = [r for r in by_role[role] if r["status"] == STATUS_KEEP and not r.get("kept_for")]
        line.sort(key=lambda r: r.get("role_percentile", 0.5))
        for row in line[:reserve_count]:
            row["status"] = STATUS_RESERVE

    problems = check_decisions(rows)
    if problems:
        raise RuntimeError("Cut decisions are inconsistent: " + "; ".join(problems))

    # `notes` are structural events (shortages, swaps); wcl.writing turns
    # them and the recorded decisions into the report's prose.
    return {"rows": _order_rows(rows), "notes": notes}
