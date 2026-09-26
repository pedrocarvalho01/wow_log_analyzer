"""Rationales and notes (roster-WRITING-GUIDE.md) on the decision guide's raid."""
from tests.test_decisions import _run, guide_raid
from wcl.rank import STATUS_FIXED
from wcl.writing import (
    _names,
    validate_note,
    validate_rationale,
    write_texts,
)


def _written():
    rows, composition, protected, config = guide_raid()
    result = _run(rows, composition, protected=protected, config=config)
    texts = write_texts(result["rows"], result["notes"], composition, config)
    return {r["name"]: r for r in result["rows"]}, texts


def test_reference_rationales():
    rows, _ = _written()
    assert rows["Giampanos"]["rationale"] == (
        "DPS within 1% of Shalammage, but a notably higher death rate; 10% below the DPS median"
    )
    assert rows["Välerjar"]["rationale"] == (
        "Second-lowest DPS, 22% below the DPS median; 13% below the lowest DPS kept (Shalammage); "
        "fourth Mage on the roster"
    )
    assert rows["Denixirian"]["rationale"].startswith(
        "Fifth healer for 4 slots; lowest HPS, 4% below the lowest HPS kept (Hasizawa)"
    )
    assert rows["Bewitcheress"]["rationale"].startswith("Lowest DPS and item level (318); 14% below the lowest DPS kept (Shalammage)")
    assert rows["Atrocion"]["rationale"] == "Main tank; absorbs most damage in raid (539m), 46.4% mitigation"
    assert rows["Ojian"]["rationale"] == "Off-tank, took 368m (41% of tank damage) with 50.9% mitigation"
    assert rows["Lifèstream"]["rationale"].startswith(
        "Highest HPS by a clear margin, 19% above the HPS median; 33% above the cut line (Denixirian)"
    )
    assert rows["Shalammage"]["rationale"] == (
        "Well below-average DPS, 11% below the DPS median; 1% below the cut line (Giampanos); "
        "offset by good survival"
    )


def test_every_ranked_rationale_quantifies_the_cut_line():
    rows, _ = _written()
    for r in rows.values():
        if r["role"] == "tank" or r.get("exclude_reason"):
            continue
        assert "%" in r["rationale"], r["name"]


def test_fixed_player_keeps_numbers_and_rationale():
    rows, _ = _written()
    assert rows["Windson"]["status"] == STATUS_FIXED
    # Described like anyone else, including where the numbers would have put them.
    assert rows["Windson"]["rationale"].startswith(
        "Third-lowest DPS, 18% below the DPS median; 8% below the lowest DPS kept (Shalammage)"
    )


def test_raid_leader_status():
    rows, composition, protected, config = guide_raid()
    from tests.test_decisions import _run as run_cuts
    from wcl.rank import STATUS_RAID_LEADER
    result = run_cuts(rows, composition, protected=protected, config=config, raid_leaders={"Windson"})
    out = {r["name"]: r for r in result["rows"]}
    assert out["Windson"]["status"] == STATUS_RAID_LEADER


def test_every_line_passes_self_review():
    _, texts = _written()
    assert texts["problems"] == []


def test_superlatives_are_true():
    rows, _ = _written()
    for role, metric in (("dps", "DPS"), ("healer", "HPS")):
        pool = [r for r in rows.values() if r["role"] == role and r["status"] != STATUS_FIXED
                and not r.get("exclude_reason")]
        lowest = min(pool, key=lambda r: r["output"])
        highest = max(pool, key=lambda r: r["output"])
        for r in pool:
            if r["rationale"].startswith(f"Lowest {metric}"):
                assert r is lowest
            if r["rationale"].startswith(f"Highest {metric}"):
                assert r is highest


def test_notes_follow_the_categories():
    _, texts = _written()
    notes = texts["notes"]
    assert 2 <= len(notes) <= 4
    assert notes[0].startswith("Erythria (Augmentation) is excluded")
    assert any("Giampanos / Shalammage decision" in n for n in notes)


def test_validators_catch_bad_lines():
    row = {"name": "X", "status": "Remove", "exclude_reason": None}
    assert validate_rationale("He has bad DPS.", row)
    assert validate_rationale("Lowest DPS; 2 early death(s)", row)
    assert validate_rationale("Lowest DPS; second-lowest survival in the raid", row) == []
    assert validate_note("Too short.")
    assert validate_note("Windson and Shalammage take well above the DPS average; this note has no final period")


def test_names_join_has_no_serial_comma():
    assert _names(["A"]) == "A"
    assert _names(["A", "B"]) == "A and B"
    assert _names(["A", "B", "C"]) == "A, B and C"
