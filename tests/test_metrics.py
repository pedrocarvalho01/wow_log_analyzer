from wcl.metrics import (
    build_player_metrics,
    duration_weighted_average,
    normalise_output,
    survival_pct_for_pull,
)


def test_survival_full_when_no_death():
    deaths = [{"id": 99, "timestamp": 5000}]
    assert survival_pct_for_pull(deaths, player_id=1, fight_start=0, duration=10000) == 100.0


def test_survival_partial_on_death():
    # Player 1 dies 3000ms into a 10000ms pull -> 30% survival.
    deaths = [{"id": 1, "timestamp": 3000}]
    result = survival_pct_for_pull(deaths, player_id=1, fight_start=0, duration=10000)
    assert result == 30.0


def test_survival_uses_first_death_when_multiple():
    deaths = [{"id": 1, "timestamp": 8000}, {"id": 1, "timestamp": 3000}]
    result = survival_pct_for_pull(deaths, player_id=1, fight_start=0, duration=10000)
    assert result == 30.0


def test_survival_respects_fight_start_offset():
    # Fight starts at 100000 absolute time; death at 103000 -> 3000ms in.
    deaths = [{"id": 1, "timestamp": 103000}]
    result = survival_pct_for_pull(deaths, player_id=1, fight_start=100000, duration=10000)
    assert result == 30.0


def test_survival_clipped_to_zero_and_hundred():
    deaths = [{"id": 1, "timestamp": -500}]  # pathological: before pull start
    result = survival_pct_for_pull(deaths, player_id=1, fight_start=0, duration=10000)
    assert result == 0.0


def test_normalise_output_uses_role_median():
    # Player output 200 vs role median 100 -> norm 2.0.
    assert normalise_output(200.0, [100.0, 90.0, 110.0]) == 2.0


def test_normalise_output_none_when_no_peers():
    assert normalise_output(200.0, []) is None


def test_normalise_output_none_when_median_zero():
    assert normalise_output(200.0, [0.0, 0.0]) is None


def test_duration_weighted_average_basic():
    # 10 over a 100ms pull and 20 over a 300ms pull -> weighted toward 20.
    result = duration_weighted_average([(10.0, 100.0), (20.0, 300.0)])
    assert round(result, 2) == 17.5


def test_duration_weighted_average_ignores_none_values():
    result = duration_weighted_average([(None, 100.0), (20.0, 300.0)])
    assert result == 20.0


def test_duration_weighted_average_none_when_no_weight():
    assert duration_weighted_average([(10.0, 0.0)]) is None


def _one_pull_report(tables: dict, start=0, end=20000) -> dict:
    """Minimal fetched-report shape for build_player_metrics: one healer (1),
    one DPS (2) and one tank (3) in a single pull."""
    details = {
        "healers": [{"id": 1, "specs": [{"spec": "Holy"}], "maxItemLevel": 320}],
        "dps": [{"id": 2, "specs": [{"spec": "Fire"}], "maxItemLevel": 320}],
        "tanks": [{"id": 3, "specs": [{"spec": "Blood"}], "maxItemLevel": 320}],
    }
    return {
        "fights": [{"id": 1, "startTime": start, "endTime": end}],
        "tables": {1: tables},
        "pet_owner": {},
        "player_details_by_fight": {1: details},
        "player_actors": {
            1: {"name": "Healer", "class": "Priest"},
            2: {"name": "Mage", "class": "Mage"},
            3: {"name": "Tank", "class": "DeathKnight"},
        },
    }


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_rates_use_analysed_window_not_full_pull():
    # 20s pull cut off at 10s by the wipe cutoff: 10k damage -> 1k DPS, not 500.
    tables = {
        "DamageDone": {"totalTime": 10000, "entries": [{"id": 2, "total": 10000, "activeTime": 9000}]},
        "Healing": {"totalTime": 10000, "entries": [{"id": 1, "total": 5000, "overheal": 0, "activeTime": 10000}]},
        "DamageTaken": {"totalTime": 10000, "entries": []},
        "Deaths": {"entries": []},
    }
    rows = _by_name(build_player_metrics(_one_pull_report(tables), {}))
    assert rows["Mage"]["output"] == 1000.0
    assert rows["Mage"]["active_pct"] == 90.0


def test_healing_total_is_already_net_of_overheal():
    # total=6000 effective, overheal=4000 -> 600 HPS over 10s and 40% overheal.
    tables = {
        "DamageDone": {"totalTime": 10000, "entries": []},
        "Healing": {"totalTime": 10000, "entries": [{"id": 1, "total": 6000, "overheal": 4000, "activeTime": 10000}]},
        "DamageTaken": {"totalTime": 10000, "entries": []},
        "Deaths": {"entries": []},
    }
    healer = _by_name(build_player_metrics(_one_pull_report(tables), {}))["Healer"]
    assert healer["output"] == 600.0
    assert healer["overheal_pct"] == 40.0


def test_mitigation_is_absorbed_share_of_incoming():
    # 540 taken after 460 absorbed -> 46% mitigated.
    tables = {
        "DamageDone": {"totalTime": 10000, "entries": [{"id": 3, "total": 1000, "activeTime": 10000}]},
        "Healing": {"totalTime": 10000, "entries": []},
        "DamageTaken": {"totalTime": 10000, "entries": [{"id": 3, "total": 540, "overheal": 460}]},
        "Deaths": {"entries": []},
    }
    tank = _by_name(build_player_metrics(_one_pull_report(tables), {}))["Tank"]
    assert tank["mitigated_pct"] == 46.0


def test_present_player_missing_from_output_table_counts_as_zero():
    # Instant wipe: the DPS only appears in DamageTaken. The pull still counts,
    # with 0 output and the damage they took.
    tables = {
        "DamageDone": {"totalTime": 10000, "entries": []},
        "Healing": {"totalTime": 10000, "entries": [{"id": 1, "total": 100, "overheal": 0, "activeTime": 10000}]},
        "DamageTaken": {"totalTime": 10000, "entries": [{"id": 2, "total": 29000000}]},
        "Deaths": {"entries": []},
    }
    mage = _by_name(build_player_metrics(_one_pull_report(tables), {}))["Mage"]
    assert mage["pulls_attended"] == 1
    assert mage["output"] == 0.0
    assert mage["damage_taken_total"] == 29000000


def test_survival_uses_full_pull_length():
    # Death 5s into a 20s pull cut off at 10s -> 25% survival, not 50%.
    tables = {
        "DamageDone": {"totalTime": 10000, "entries": [{"id": 2, "total": 10000, "activeTime": 5000}]},
        "Healing": {"totalTime": 10000, "entries": []},
        "DamageTaken": {"totalTime": 10000, "entries": []},
        "Deaths": {"entries": [{"id": 2, "timestamp": 5000}]},
    }
    mage = _by_name(build_player_metrics(_one_pull_report(tables), {}))["Mage"]
    assert mage["survival_pct"] == 25.0
