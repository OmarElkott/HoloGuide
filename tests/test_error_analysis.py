import json

import pytest

from config import load_config
from vision.error_analysis import (go_no_go, load_rows, normalize_category, render_markdown,
                                   run_dataset, summarize, write_csv)
from vision.inspector import MockInspector


@pytest.fixture
def dataset(tmp_path):
    rows = [
        {"image": "a.png", "step_id": 2, "mistake_type": "correct", "parts_present": ["base_plate", "gasket"]},
        {"image": "b.png", "step_id": 2, "mistake_type": "correct", "parts_present": ["base_plate", "gasket"]},
        {"image": "c.png", "step_id": 3, "mistake_type": "missing_gasket", "parts_present": ["base_plate", "cover"]},
        {"file": "d.png", "step": 3, "category": "wrong_cover"},
    ]
    for r in rows:
        (tmp_path / (r.get("image") or r["file"])).write_bytes(b"x")
    labels = tmp_path / "labels.jsonl"
    labels.write_text("\n".join(json.dumps(r) for r in rows))
    return tmp_path, labels


def test_category_normalisation():
    assert [normalize_category(x) for x in
            ["correct", "missing_gasket", "wrong_cover", "wrong_order", "out-of-order", "ambiguous", None]] == \
           ["correct", "missing", "wrong", "order", "order", "ambiguous", "correct"]


def test_load_rows_accepts_aliases_and_derives_expected_status(dataset):
    folder, labels = dataset
    rows = load_rows(labels, folder)
    assert [r.category for r in rows] == ["correct", "correct", "missing", "wrong"]
    assert [r.expected_status for r in rows] == ["pass", "pass", "block", "block"]
    assert rows[3].step_id == 3


def test_loader_rejects_rows_without_image_or_step(tmp_path):
    p = tmp_path / "l.json"
    p.write_text(json.dumps([{"mistake_type": "correct"}]))
    with pytest.raises(ValueError):
        load_rows(p, tmp_path)


def test_end_to_end_report_with_mock_inspector(dataset, tmp_path):
    folder, labels = dataset
    cfg = load_config()
    results = run_dataset(MockInspector(cfg, mode="correct"), cfg, load_rows(labels, folder))
    summary = summarize(results, cfg)
    # A mock that always says "everything is fine" must be caught as false accepts.
    assert summary["categories"]["correct"]["accuracy"] == 1.0
    assert summary["categories"]["missing"]["false_accepts"] == 1
    assert summary["categories"]["wrong"]["false_accepts"] == 1
    assert summary["false_accepts_total"] == 2
    assert summary["go_no_go"]["option"] == "C"          # missing/wrong accuracy is 0%
    write_csv(results, tmp_path / "out" / "r.csv")
    assert (tmp_path / "out" / "r.csv").read_text().count("\n") == 5
    assert "Worst failures" in render_markdown(summary, results, {"model": "mock"})


def test_perfect_inspector_gets_option_a():
    summary = {"categories": {"missing": {"accuracy": 0.95}, "wrong": {"accuracy": 0.9}},
               "false_block_rate": 0.02, "false_accepts_total": 0, "memory_errors": 0,
               "latency": {"median_s": 4.0}}
    assert go_no_go(summary)["option"] == "A"
    summary["latency"]["median_s"] = 14.0
    assert go_no_go(summary)["option"] == "B"
    summary["latency"]["median_s"] = 25.0
    assert go_no_go(summary)["option"] == "C"
