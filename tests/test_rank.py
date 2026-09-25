from collections import Counter

from wcl.rank import (
    STATUS_KEEP,
    STATUS_NOT_EVALUATED,
    STATUS_REMOVE,
    STATUS_RESERVE,
    _class_redundancy_pick,
    _near_tie,
    propose_cuts,
)

CUT_RULES = {
    "near_tie_output_pct": 2.0,
    "near_tie_survival_pp": 2.0,
    "borderline_output_pct": 5.0,
    "borderline_survival_pp": 3.0,
    "reserve_count": 0,
    "min_bloodlust_sources": 2,
}


def _player(name, cls, role, output_norm, survival, **overrides):
    row = {
        "id": hash(name) % 10_000,
        "name": name,
        "class": cls,
        "spec": "Test",
        "role": role,
        "ilvl": 320,
        "pulls_attended": 10,
        "total_pulls": 10,
        "output": 100.0,
        "output_norm": output_norm,
        "active_pct": 90.0,
        "survival_pct": survival,
        "deaths": 0,
        "early_deaths": 0,
        "damage_taken_total": 1000,
        "damage_taken_rate": 10.0,
        "mitigated_pct": 0.0,
        "overheal_pct": None,
        "low_sample": False,
        "exclude_reason": None,
        "score": output_norm,
        "role_percentile": None,
        "essential": False,
    }
    row.update(overrides)
    return row


def test_near_tie_detects_close_output_norm():
    a = _player("A", "Mage", "dps", 1.00, 95)
    b = _player("B", "Mage", "dps", 1.01, 95)  # 1% apart
    assert _near_tie(a, b, CUT_RULES) is True


def test_near_tie_false_when_far_apart():
    a = _player("A", "Mage", "dps", 1.00, 95)
    b = _player("B", "Mage", "dps", 1.10, 95)  # 10% apart
    assert _near_tie(a, b, CUT_RULES) is False


def test_class_redundancy_picks_more_common_class():
    kept_classes = Counter({"Mage": 4, "Hunter": 1})
    a = _player("A", "Mage", "dps", 1.0, 95)
    b = _player("B", "Hunter", "dps", 1.0, 95)
    # The 4th Mage should be cut before the 2nd Hunter.
    assert _class_redundancy_pick([a, b], kept_classes)["name"] == "A"


def _config(target_composition):
    return {
        "cut_rules": CUT_RULES,
        "raid_buffs": {"Arcane Intellect": ["Mage"]},
        "bloodlust_classes": ["Shaman", "Mage"],
    }


def test_near_tie_rule_prefers_cutting_lower_survival():
    # Two dps candidates sit right at the cut boundary with near-identical
    # output_norm; the one with clearly worse survival should be the one
    # actually removed, per BUILD.md's near-tie rule.
    kept_dps = _player("Kept1", "Warrior", "dps", 1.50, 95, role_percentile=0.9)
    edge_low = _player("EdgeLow", "Hunter", "dps", 1.00, 80.0, role_percentile=0.3)   # worse survival
    edge_high = _player("EdgeHigh", "Rogue", "dps", 1.01, 96.0, role_percentile=0.35)  # near-tied output, better survival

    rows = [kept_dps, edge_low, edge_high]
    for r in rows:
        r["role"] = "dps"
    result = propose_cuts(
        rows, target=2, composition={"tank": 0, "healer": 0, "dps": 2},
        protected_names=set(), config=_config({"dps": 2}),
    )
    statuses = {r["name"]: r["status"] for r in result["rows"]}
    assert statuses["EdgeLow"] == STATUS_REMOVE
    assert statuses["EdgeHigh"] == STATUS_KEEP


def test_buff_coverage_swap_restores_last_buff_source():
    # Only Mage on the roster provides Arcane Intellect. If cutting the two
    # worst dps would remove that Mage, the cut list must swap to spare it.
    mage = _player("OnlyMage", "Mage", "dps", 0.50, 90.0, role_percentile=0.0)   # worst -> would be cut
    hunter = _player("Hunter1", "Hunter", "dps", 0.55, 90.0, role_percentile=0.1)  # 2nd worst -> would be cut
    kept1 = _player("Kept1", "Warrior", "dps", 1.5, 95.0, role_percentile=0.6)
    kept2 = _player("Kept2", "Rogue", "dps", 1.6, 96.0, role_percentile=0.8)

    rows = [mage, hunter, kept1, kept2]
    for r in rows:
        r["role"] = "dps"

    result = propose_cuts(
        rows, target=2, composition={"tank": 0, "healer": 0, "dps": 2},
        protected_names=set(), config=_config({"dps": 2}),
    )
    statuses = {r["name"]: r["status"] for r in result["rows"]}
    assert statuses["OnlyMage"] == STATUS_KEEP
    assert statuses["Hunter1"] == STATUS_REMOVE
    assert any("Arcane Intellect" in note for note in result["notes"])


def _dps_pool(n):
    return [
        _player(f"D{i}", "Warrior", "dps", 1.0 + i / 10, 95.0, role_percentile=i / n)
        for i in range(n)
    ]


def _kept_count(result):
    return sum(1 for r in result["rows"] if r["status"] != STATUS_REMOVE)


def test_protected_player_fills_a_slot():
    rows = _dps_pool(5)
    result = propose_cuts(
        rows, target=3, composition={"tank": 0, "healer": 0, "dps": 3},
        protected_names={"D0"}, config=_config({"dps": 3}),
    )
    statuses = {r["name"]: r["status"] for r in result["rows"]}
    assert statuses["D0"] == STATUS_NOT_EVALUATED
    assert _kept_count(result) == 3


def test_excluded_player_fills_a_slot_and_is_kept():
    rows = _dps_pool(5)
    rows[0]["exclude_reason"] = "support_spec"
    result = propose_cuts(
        rows, target=3, composition={"tank": 0, "healer": 0, "dps": 3},
        protected_names=set(), config=_config({"dps": 3}),
    )
    statuses = {r["name"]: r["status"] for r in result["rows"]}
    assert statuses["D0"] == STATUS_KEEP
    assert _kept_count(result) == 3


def test_protected_overflow_moves_cuts_to_dps():
    healers = [
        _player(f"H{i}", "Priest", "healer", 1.0, 95.0, role_percentile=i / 3) for i in range(3)
    ]
    rows = healers + _dps_pool(4)
    result = propose_cuts(
        rows, target=5, composition={"tank": 0, "healer": 2, "dps": 3},
        protected_names={"H0", "H1", "H2"}, config=_config({}),
    )
    statuses = {r["name"]: r["status"] for r in result["rows"]}
    assert all(statuses[f"H{i}"] == STATUS_NOT_EVALUATED for i in range(3))
    assert sum(1 for s in statuses.values() if s == STATUS_REMOVE) == 2
    assert _kept_count(result) == 5
    assert any("cannot be cut" in n for n in result["notes"])


def test_reserve_only_in_roles_with_cuts():
    config = _config({})
    config["cut_rules"] = {**CUT_RULES, "reserve_count": 1}
    healers = [
        _player(f"H{i}", "Priest", "healer", 1.0, 95.0, role_percentile=i / 2) for i in range(2)
    ]
    rows = healers + _dps_pool(4)
    result = propose_cuts(
        rows, target=5, composition={"tank": 0, "healer": 2, "dps": 3},
        protected_names=set(), config=config,
    )
    by_role = {}
    for r in result["rows"]:
        by_role.setdefault(r["role"], []).append(r["status"])
    assert STATUS_RESERVE not in by_role["healer"]
    assert by_role["dps"].count(STATUS_RESERVE) == 1
