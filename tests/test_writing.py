"""Rationales and notes (roster-WRITING-GUIDE.md) on the decision guide's raid."""
from tests.test_decisions import _run, guide_raid
from wcl.rank import STATUS_NOT_EVALUATED
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
    assert rows["Giampanos"]["rationale"] == "DPS on par with Shalammage, but a notably higher death rate"
    assert rows["Välerjar"]["rationale"] == "Second-lowest DPS; fourth Mage on the roster"
    assert rows["Denixirian"]["rationale"].startswith("Fifth healer; lowest HPS, 4% below Hasizawa")
    assert rows["Bewitcheress"]["rationale"].startswith("Lowest DPS and item level (318)")
    assert rows["Atrocion"]["rationale"] == "Main tank; absorbs most damage in raid (539m), 46.4% mitigation"
    assert rows["Ojian"]["rationale"] == "Stable tanking with 50.9% mitigation"
    assert rows["Lifèstream"]["rationale"] == "Highest HPS by a clear margin, with no deaths"
    assert rows["Shalammage"]["rationale"].endswith("offset by good survival")


def test_protected_player_is_not_evaluated_and_never_mentioned():
    rows, texts = _written()
    assert rows["Windson"]["status"] == STATUS_NOT_EVALUATED
    assert rows["Windson"]["rationale"] == "Not evaluated"
    others = [r["rationale"] for n, r in rows.items() if n != "Windson"]
    assert not any("Windson" in t for t in others + texts["notes"])


def test_every_line_passes_self_review():
    _, texts = _written()
    assert texts["problems"] == []


def test_superlatives_are_true():
    rows, _ = _written()
    for role, metric in (("dps", "DPS"), ("healer", "HPS")):
        pool = [r for r in rows.values() if r["role"] == role and r["status"] != STATUS_NOT_EVALUATED
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
    assert notes[0].startswith("Erythria (Augmentation) was excluded")
    assert any("Giampanos / Shalammage decision" in n for n in notes)


def test_validators_catch_bad_lines():
    row = {"name": "X", "status": "Remove", "exclude_reason": None}
    assert validate_rationale("He has bad DPS.", row)
    assert validate_rationale("Lowest DPS; 2 early death(s)", row)
    assert validate_rationale("Lowest DPS; second-lowest survival in the raid", row) == []
    assert validate_note("Too short.", set())
    assert validate_note(
        "Windson and Shalammage take well above the DPS average (~215m); an ability breakdown may reveal avoidable damage.",
        {"Windson"},
    )


def test_names_join_has_no_serial_comma():
    assert _names(["A"]) == "A"
    assert _names(["A", "B"]) == "A and B"
    assert _names(["A", "B", "C"]) == "A, B and C"
