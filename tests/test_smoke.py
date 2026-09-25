"""End-to-end smoke test: run.py on the cached reference report, offline.

Runs the whole pipeline (metrics -> decisions -> writing -> CSV -> one-page
PDF) from the on-disk cache. Skipped when the cache isn't there (it is
gitignored) or Playwright's Chromium isn't installed.
"""
import csv
from collections import Counter
from pathlib import Path

import pytest
import yaml

import run
import wcl.fetch

ROOT = Path(__file__).resolve().parent.parent
REPORT_CODE = "VrH3tjbQzWCaywMd"
CACHE_DIR = ROOT / "cache"


@pytest.fixture
def offline_config(tmp_path, monkeypatch):
    if not (CACHE_DIR / REPORT_CODE).is_dir():
        pytest.skip("cached reference report not available")

    def no_network(*_args, **_kwargs):
        raise AssertionError("smoke test must run from the cache only")

    monkeypatch.setattr(wcl.fetch, "graphql", no_network)

    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["output"]["cache_dir"] = str(CACHE_DIR)
    config["output"]["out_dir"] = str(tmp_path / "out")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path, tmp_path / "out"


def test_full_run_from_cache(offline_config, capsys):
    pytest.importorskip("playwright")
    config_path, out_dir = offline_config

    try:
        exit_code = run.main([
            f"https://www.warcraftlogs.com/reports/{REPORT_CODE}", "--config", str(config_path),
        ])
    except Exception as exc:  # Chromium missing is an environment problem, not a failure
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Playwright Chromium not installed")
        raise
    assert exit_code == 0

    (run_dir,) = (out_dir / REPORT_CODE).iterdir()
    with (run_dir / "metrics.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 24
    kept = [r for r in rows if r["status"] != "Remove"]
    assert Counter(r["role"] for r in kept) == {"tank": 2, "healer": 4, "dps": 14}
    assert sorted(r["name"] for r in rows if r["status"] == "Remove") == [
        "Bewitcheress", "Hasizawa", "Välerjar", "Windson",
    ]
    assert all(r["rationale"] for r in rows)
    assert {"Enzð", "Lifèstream"} <= {r["name"] for r in rows}

    from pypdf import PdfReader

    assert len(PdfReader(str(run_dir / "roster_review.pdf")).pages) == 1
    assert (run_dir / "roster_review.png").exists()

    printed = capsys.readouterr()
    assert "Writing check" not in printed.err
    assert "Notes:" in printed.out
