from datetime import datetime

from wcl.library import run_dir, subject_slug, write_catalog

WHEN = datetime(2026, 9, 25, 16, 38, 9)


def test_run_dir_shelves_by_zone_and_names_by_date_first(tmp_path):
    path = run_dir(tmp_path, "The Venomous Abyss", "Falkien The Lost Explorers Heroic", WHEN)
    assert path == tmp_path / "the-venomous-abyss" / "2026-09-25_1638_falkien-the-lost-explorers-heroic"


def test_run_dir_without_zone_goes_to_unsorted_and_never_reuses_a_folder(tmp_path):
    first = run_dir(tmp_path, None, "x", WHEN)
    first.mkdir(parents=True)
    assert first.parent.name == "unsorted"
    assert run_dir(tmp_path, None, "x", WHEN).name == f"{first.name}-2"


def test_subject_slug_cuts_long_titles_at_a_word():
    s = subject_slug("thursday first AOTC raid after AOTC and then a very long tail of words")
    assert len(s) <= 48 and not s.endswith("-")


def test_catalog_lists_newest_first_with_links(tmp_path):
    for zone, subject, hour in [("A", "old", 10), ("B", "new", 12)]:
        d = run_dir(tmp_path, zone, subject, WHEN.replace(hour=hour))
        d.mkdir(parents=True)
        (d / f"{d.name}.pdf").write_bytes(b"%PDF")
    rows = [l for l in write_catalog(tmp_path).read_text(encoding="utf-8").splitlines() if l.startswith("| 2026")]
    assert rows[0].startswith("| 2026-09-25 12:38 | b | new |")
    assert "(<b/2026-09-25_1238_new/2026-09-25_1238_new.pdf>)" in rows[0]
    assert rows[1].startswith("| 2026-09-25 10:38 | a | old |")
