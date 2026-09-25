"""Decision-guide behaviour (roster-DECISION-GUIDE.md), end to end through
score_players -> build_overall_order -> propose_cuts."""
from wcl.rank import (
    CONFIDENCE_CLEAR,
    CONFIDENCE_CLOSE_CALL,
    CONFIDENCE_SUPPORTED,
    STATUS_ESSENTIAL,
    STATUS_KEEP,
    STATUS_NOT_EVALUATED,
    STATUS_REMOVE,
    STATUS_RESERVE,
    build_overall_order,
    check_decisions,
    propose_cuts,
    score_players,
)

CONFIG = {
    "scoring": {"tank": {"survival": 0.5, "mitigation": 0.3, "damage_taken_share": 0.2}},
    "cut_rules": {
        "near_tie_output_pct": 2.0,
        "near_tie_survival_pp": 2.0,
        "borderline_output_pct": 5.0,
        "borderline_survival_pp": 3.0,
        "reserve_count": 1,
        "min_bloodlust_sources": 0,
        "min_battle_res_sources": 0,
    },
}


def _p(name, cls, role, norm, survival=95.0, **extra):
    row = {
        "name": name, "class": cls, "spec": "Test", "role": role, "ilvl": 323,
        "pulls_attended": 15, "total_pulls": 15, "output": norm * 100_000,
        "output_norm": norm, "active_pct": 95.0, "survival_pct": survival,
        "deaths": 0 if survival >= 100 else 1, "early_deaths": 0, "damage_taken_total": 200e6,
        "damage_taken_rate": 60_000.0, "mitigated_pct": 20.0, "overheal_pct": None,
        "low_sample": False, "exclude_reason": None,
    }
    row.update(extra)
    return row


def _run(rows, composition, protected=(), config=CONFIG):
    target = sum(composition.values())
    rows = score_players(rows, config)
    rows = build_overall_order(rows, set(protected), composition)
    return propose_cuts(rows, target, composition, set(protected), config)


def _by_name(result):
    return {r["name"]: r for r in result["rows"]}


def test_throughput_decides_not_damage_taken():
    # A: lowest output but takes little damage; B: 15% more output, lots of damage.
    rows = [
        _p("A", "Mage", "dps", 0.80, damage_taken_rate=40_000.0),
        _p("B", "Rogue", "dps", 0.95, damage_taken_rate=90_000.0),
        _p("C", "Warrior", "dps", 1.10),
        _p("D", "Hunter", "dps", 1.15),
    ]
    out = _by_name(_run(rows, {"tank": 0, "healer": 0, "dps": 3}))
    assert out["A"]["status"] == STATUS_REMOVE
    assert out["A"]["confidence"] == CONFIDENCE_CLEAR
    assert out["B"]["status"] == STATUS_RESERVE


def test_borderline_gap_supported_when_survival_clearly_better():
    rows = [
        _p("Cut", "Mage", "dps", 0.96, survival=90.0),
        _p("Kept", "Rogue", "dps", 1.00, survival=96.0),  # 4% gap, +6 pp survival
        _p("Top", "Warrior", "dps", 1.20),
    ]
    out = _by_name(_run(rows, {"tank": 0, "healer": 0, "dps": 2}))
    assert out["Cut"]["confidence"] == CONFIDENCE_SUPPORTED


def test_borderline_gap_close_call_when_secondaries_disagree():
    rows = [
        _p("Cut", "Mage", "dps", 0.96, survival=97.0),
        _p("Kept", "Rogue", "dps", 1.00, survival=92.0),  # 4% gap, worse survival
        _p("Top", "Warrior", "dps", 1.20),
    ]
    result = _run(rows, {"tank": 0, "healer": 0, "dps": 2})
    out = _by_name(result)
    assert out["Cut"]["confidence"] == CONFIDENCE_CLOSE_CALL
    assert out["Cut"]["near_tie_peer"] == "Kept"


def test_near_tie_cuts_lower_survival_and_reorders():
    rows = [
        _p("LowOut", "Mage", "dps", 0.990, survival=97.0),
        _p("HighOut", "Hunter", "dps", 1.000, survival=92.0),  # 1% more output, 5 pp worse survival
        _p("Top", "Warrior", "dps", 1.20),
    ]
    out = _by_name(_run(rows, {"tank": 0, "healer": 0, "dps": 2}))
    assert out["HighOut"]["status"] == STATUS_REMOVE
    assert out["HighOut"]["confidence"] == CONFIDENCE_CLOSE_CALL
    assert out["HighOut"]["tie_basis"] == "survival"
    assert out["HighOut"]["rank"] < out["LowOut"]["rank"]


def test_near_tie_flags_class_redundancy_conflict():
    rows = [
        _p("Hunt", "Hunter", "dps", 1.000, survival=92.0),
        _p("Mage1", "Mage", "dps", 0.995, survival=97.0),
        _p("Mage2", "Mage", "dps", 1.20),
        _p("Mage3", "Mage", "dps", 1.25),
    ]
    out = _by_name(_run(rows, {"tank": 0, "healer": 0, "dps": 3}))
    assert out["Hunt"]["status"] == STATUS_REMOVE
    assert out["Hunt"]["tie_conflict"] is True


def test_protected_player_ranked_by_numbers_and_skipped():
    rows = [
        _p("Low", "Mage", "dps", 0.80),
        _p("Lead", "Paladin", "dps", 0.85),
        _p("Mid", "Rogue", "dps", 0.95),
        _p("Top", "Warrior", "dps", 1.20),
    ]
    result = _run(rows, {"tank": 0, "healer": 0, "dps": 2}, protected={"Lead"})
    out = _by_name(result)
    assert out["Lead"]["status"] == STATUS_NOT_EVALUATED
    assert out["Lead"]["rank"] == 2  # natural position, not pinned to the end
    assert [r["name"] for r in result["rows"] if r["status"] == STATUS_REMOVE] == ["Low", "Mid"]


def test_low_attendance_is_excluded_from_cut():
    rows = [
        _p("Rare", "Mage", "dps", 0.50, pulls_attended=3, exclude_reason="low_sample"),
        _p("A", "Rogue", "dps", 0.90),
        _p("B", "Warrior", "dps", 1.10),
        _p("C", "Hunter", "dps", 1.20),
    ]
    out = _by_name(_run(rows, {"tank": 0, "healer": 0, "dps": 3}))
    assert out["Rare"]["status"] == STATUS_KEEP
    assert out["A"]["status"] == STATUS_REMOVE


def test_battle_res_swap_keeps_two_sources():
    config = {
        **CONFIG,
        "cut_rules": {**CONFIG["cut_rules"], "min_battle_res_sources": 2},
        "battle_res_classes": ["Druid", "DeathKnight", "Warlock", "Paladin"],
    }
    rows = [
        _p("Lock", "Warlock", "dps", 0.80),
        _p("Rogue", "Rogue", "dps", 0.90),
        _p("Druid", "Druid", "dps", 1.10),
        _p("Mage", "Mage", "dps", 1.20),
    ]
    result = _run(rows, {"tank": 0, "healer": 0, "dps": 3}, config=config)
    out = _by_name(result)
    assert out["Lock"]["status"] == STATUS_KEEP
    assert out["Lock"]["kept_for"] == "battle res"
    assert out["Rogue"]["status"] == STATUS_REMOVE
    assert out["Rogue"]["cut_for_composition"] == "battle res"
    assert check_decisions(result["rows"]) == []


def test_buff_config_matches_spaced_class_names():
    config = {**CONFIG, "raid_buffs": {"Chaos Brand": ["Demon Hunter"]}}
    rows = [
        _p("DH", "DemonHunter", "dps", 0.80),
        _p("Rogue", "Rogue", "dps", 0.90),
        _p("Mage", "Mage", "dps", 1.10),
    ]
    out = _by_name(_run(rows, {"tank": 0, "healer": 0, "dps": 2}, config=config))
    assert out["DH"]["status"] == STATUS_KEEP
    assert out["Rogue"]["status"] == STATUS_REMOVE


def test_short_role_is_reported():
    rows = [_p("H1", "Priest", "healer", 1.0), _p("D1", "Mage", "dps", 1.0)]
    result = _run(rows, {"tank": 0, "healer": 2, "dps": 1})
    assert any("short" in n for n in result["notes"])


def test_check_decisions_flags_remove_above_keep():
    rows = [
        {"name": "A", "role": "dps", "status": STATUS_REMOVE, "role_percentile": 0.9},
        {"name": "B", "role": "dps", "status": STATUS_KEEP, "role_percentile": 0.1},
    ]
    assert check_decisions(rows) == ["A (Remove) ranks above B (Keep)"]


def guide_raid():
    """The raid from roster-DECISION-GUIDE.md §10 (guide figures; Windson protected).
    Returns (rows, composition, protected, config)."""
    dps = [
        _p("Bewitcheress", "Priest", "dps", 0.785, survival=87.9, active_pct=85.5, ilvl=318),
        _p("Välerjar", "Mage", "dps", 0.795, survival=96.3),
        _p("Windson", "Paladin", "dps", 0.843, survival=89.6),
        _p("Shalammage", "Mage", "dps", 0.914, survival=96.8),
        _p("Giampanos", "Hunter", "dps", 0.922, survival=92.3),
        _p("Darksaiko", "Hunter", "dps", 0.977, survival=93.2),
        _p("Nastra", "Rogue", "dps", 0.998, survival=100.0),
        _p("Averno", "DemonHunter", "dps", 1.013, survival=92.7),
        _p("Sangeia", "Warrior", "dps", 1.035, survival=93.9),
        _p("Vulcanea", "Mage", "dps", 1.037, survival=95.2),
        _p("Enzð", "Mage", "dps", 1.067, survival=100.0),
        _p("Hiroseer", "Shaman", "dps", 1.072, survival=91.3),
        _p("Panyc", "Warlock", "dps", 1.073, survival=96.0, damage_taken_rate=90_000.0),
        _p("Hexious", "Paladin", "dps", 1.100, survival=100.0),
        _p("Falkien", "Rogue", "dps", 1.112, survival=100.0),
        _p("Saehontas", "Druid", "dps", 1.200, survival=96.2),
        _p("Erythria", "Evoker", "dps", 1.016, survival=99.2, spec="Augmentation", exclude_reason="support_spec"),
    ]
    healers = [
        _p("Denixirian", "Evoker", "healer", 0.940, survival=98.3, active_pct=98.5, overheal_pct=39.5),
        _p("Hasizawa", "Shaman", "healer", 0.979, survival=100.0, active_pct=99.0, overheal_pct=28.8),
        _p("Ashiea", "Druid", "healer", 1.05, survival=98.9, overheal_pct=52.0),
        _p("Nictocin", "Shaman", "healer", 1.10, survival=87.5, overheal_pct=28.1),
        _p("Lifèstream", "Priest", "healer", 1.25, survival=100.0, overheal_pct=43.2),
    ]
    tanks = [
        _p("Atrocion", "DeathKnight", "tank", 1.0, survival=99.1, mitigated_pct=46.4, damage_taken_total=539e6),
        _p("Ojian", "Monk", "tank", 1.0, survival=98.8, mitigated_pct=50.9, damage_taken_total=368e6),
    ]
    config = {
        **CONFIG,
        "cut_rules": {**CONFIG["cut_rules"], "min_bloodlust_sources": 2, "min_battle_res_sources": 2},
        "raid_buffs": {"Fortitude": ["Priest"], "Arcane Intellect": ["Mage"], "Hunter's Mark": ["Hunter"]},
        "bloodlust_classes": ["Shaman", "Mage", "Evoker", "Hunter"],
        "battle_res_classes": ["Druid", "DeathKnight", "Warlock", "Paladin"],
    }
    return dps + healers + tanks, {"tank": 2, "healer": 4, "dps": 14}, {"Windson"}, config


def test_decision_guide_worked_example():
    """roster-DECISION-GUIDE.md §10: 24 players, target 20, Windson protected."""
    rows, composition, protected, config = guide_raid()
    result = _run(rows, composition, protected=protected, config=config)
    out = _by_name(result)

    removed = {n: r["confidence"] for n, r in out.items() if r["status"] == STATUS_REMOVE}
    assert removed == {
        "Denixirian": CONFIDENCE_SUPPORTED,
        "Bewitcheress": CONFIDENCE_CLEAR,
        "Välerjar": CONFIDENCE_CLEAR,
        "Giampanos": CONFIDENCE_CLOSE_CALL,
    }
    assert out["Shalammage"]["status"] == STATUS_RESERVE
    assert out["Giampanos"]["near_tie_peer"] == "Shalammage"
    assert out["Giampanos"]["tie_conflict"] is True  # Shalammage is one of 4 Mages
    assert out["Windson"]["status"] == STATUS_NOT_EVALUATED
    assert out["Erythria"]["status"] == STATUS_KEEP
    assert out["Atrocion"]["status"] == out["Ojian"]["status"] == STATUS_ESSENTIAL
    assert sum(1 for r in result["rows"] if r["status"] != STATUS_REMOVE) == 20
    assert check_decisions(result["rows"]) == []
