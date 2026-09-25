from wcl.rank import STATUS_KEEP, STATUS_NOT_EVALUATED
from wcl.report import render_markdown_table


def _row(**overrides):
    row = {
        "rank": 1,
        "name": "TestPlayer",
        "role": "dps",
        "class": "Mage",
        "output": 148800,
        "survival_pct": 87.9,
        "active_pct": 85.5,
        "status": STATUS_KEEP,
        "rationale": "Solid performance.",
    }
    row.update(overrides)
    return row


def test_markdown_table_has_header_and_row():
    md = render_markdown_table([_row()])
    assert "| # | Player | Role (Class) | Output | Survival | Active | Status | Rationale |" in md
    assert "TestPlayer" in md
    assert "148.8k" in md


def test_markdown_table_blanks_protected_player_numbers():
    md = render_markdown_table([_row(name="RaidLeader", status=STATUS_NOT_EVALUATED, rationale="Not evaluated")])
    lines = [l for l in md.splitlines() if "RaidLeader" in l]
    assert len(lines) == 1
    assert "Not evaluated" in lines[0]
    # Protected players show no performance numbers.
    assert lines[0].count("- |") >= 3


def _full_row(**overrides):
    row = _row(spec="Fire", ilvl=320, pulls_attended=10, total_pulls=10)
    row.update(overrides)
    return row


def test_pct_format_perfect_score_has_no_decimal():
    from wcl.report import _fmt_pct

    assert _fmt_pct(100.0) == "100%"
    assert _fmt_pct(99.96) == "100%"
    assert _fmt_pct(87.9) == "87.9%"


def test_survival_class_thresholds():
    from wcl.report import _surv_class

    assert _surv_class(89.9) == "bad"
    assert _surv_class(90.0) == "warn"
    assert _surv_class(94.9) == "warn"
    assert _surv_class(95.0) == ""


def test_pdf_context_rows_and_kpis():
    from wcl.rank import STATUS_ESSENTIAL, STATUS_REMOVE
    from wcl.report import build_pdf_context

    rows = [
        _full_row(rank=1, name="Cut", status=STATUS_REMOVE),
        _full_row(rank=2, name="Lead", status=STATUS_NOT_EVALUATED, rationale="Not evaluated"),
        _full_row(rank=3, name="Tank", role="tank", **{"class": "DeathKnight"}, status=STATUS_ESSENTIAL),
    ]
    rows[0]["class"] = "Priest"
    ctx = build_pdf_context(
        rows, report_title="t", report_code="ABC", target=2,
        composition={"tank": 1, "healer": 0, "dps": 1}, wipe_cutoff=3, notes=[],
    )
    players = {p["name"]: p for p in ctx["players"]}
    assert players["Cut"]["status_slug"] == "remove"
    assert players["Cut"]["class_hex"] == "#9A9A9A"
    assert players["Cut"]["surv_class"] == "bad"
    assert players["Tank"]["cls"] == "Death Knight"
    assert players["Tank"]["class_hex"] == "#C41E3A"
    # Protected player: no numbers, grey slug.
    assert players["Lead"]["status_slug"] == "raidleader"
    assert players["Lead"]["output"] == players["Lead"]["survival"] == "—"
    assert players["Lead"]["surv_class"] == ""
    assert ctx["removed"] == ["Cut"]
    assert (ctx["n_tanks"], ctx["n_dps"]) == (1, 1)
    assert ctx["notes"] == []


def test_pdf_context_caps_notes():
    from wcl.report import build_pdf_context

    ctx = build_pdf_context(
        [_full_row()], report_title="t", report_code="ABC", target=1,
        composition={"tank": 0, "healer": 0, "dps": 1}, wipe_cutoff=3,
        notes=["a", "b", "c", "d", "e"],
    )
    assert ctx["notes"] == ["a", "b", "c", "d"]


def test_report_link():
    from wcl.report import report_link

    assert report_link("VrH3tjbQzWCaywMd", 3) == (
        "https://www.warcraftlogs.com/reports/VrH3tjbQzWCaywMd?boss=-2&difficulty=0&cutoff=3"
    )
    assert report_link("ABC", 2, fight_id=7).endswith("cutoff=2&fight=7")


def test_player_names_survive_byte_for_byte(tmp_path):
    """Names from WCL (e.g. Enzð with eth, Lifèstream) must reach every output unchanged."""
    import csv

    from wcl.report import build_pdf_context, write_csv

    names = ["Enzð", "Lifèstream", "Välerjar"]
    rows = [_full_row(rank=i + 1, name=n) for i, n in enumerate(names)]

    path = tmp_path / "metrics.csv"
    write_csv(rows, path)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM, so Excel reads UTF-8
    with path.open(encoding="utf-8-sig") as f:
        assert [r["name"] for r in csv.DictReader(f)] == names

    md = render_markdown_table(rows)
    assert all(n in md for n in names)

    ctx = build_pdf_context(
        rows, report_title="t", report_code="ABC", target=3,
        composition={"tank": 0, "healer": 0, "dps": 3}, wipe_cutoff=3, notes=[],
    )
    assert [p["name"] for p in ctx["players"]] == names


def test_pdf_context_groups_sort_by_status_then_rank():
    from wcl.rank import STATUS_KEEP, STATUS_REMOVE, STATUS_RESERVE
    from wcl.report import build_pdf_context

    rows = [
        _full_row(rank=1, name="WeakKeep", status=STATUS_KEEP),
        _full_row(rank=2, name="Bench", status=STATUS_RESERVE),
        _full_row(rank=3, name="SwappedOut", status=STATUS_REMOVE),
        _full_row(rank=4, name="StrongKeep", status=STATUS_KEEP),
        _full_row(rank=0, name="WorstCut", status=STATUS_REMOVE),
    ]
    ctx = build_pdf_context(
        rows, report_title="t", report_code="ABC", target=3,
        composition={"tank": 0, "healer": 0, "dps": 3}, wipe_cutoff=3, notes=[],
    )
    assert [p["name"] for p in ctx["groups"][0]["players"]] == [
        "WorstCut", "SwappedOut", "Bench", "WeakKeep", "StrongKeep",
    ]


def test_pdf_context_groups_by_role_worst_first():
    from wcl.rank import STATUS_ESSENTIAL, STATUS_KEEP, STATUS_REMOVE
    from wcl.report import build_pdf_context

    rows = [
        _full_row(rank=3, name="GoodDps", status=STATUS_KEEP),
        _full_row(rank=1, name="BadDps", status=STATUS_REMOVE),
        _full_row(rank=2, name="Heal", role="healer", status=STATUS_KEEP),
        _full_row(rank=4, name="Tank", role="tank", status=STATUS_ESSENTIAL),
    ]
    ctx = build_pdf_context(
        rows, report_title="t", report_code="ABC", target=3,
        composition={"tank": 1, "healer": 1, "dps": 1}, wipe_cutoff=3, notes=[],
    )
    groups = ctx["groups"]
    assert [g["label"] for g in groups] == ["Tanks", "Healers", "DPS"]
    dps = groups[2]
    assert [p["name"] for p in dps["players"]] == ["BadDps", "GoodDps"]
    assert (dps["total"], dps["kept"], dps["removed"]) == (2, 1, 1)
    assert groups[0]["removed"] == 0
