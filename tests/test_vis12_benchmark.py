"""VIS-12 latency benchmark: timing, timeout counting, N/A reporting, no overwrite."""
import csv

import pytest

from src.config import load_config
from src.vision.inspector import MockInspector, SlowInspector, TimeoutInspector
from src.vision.latency_benchmark import load_cases, measure, run

CFG = load_config()


@pytest.fixture
def manifest(tmp_path):
    (tmp_path / "images").mkdir()
    path = tmp_path / "labels.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["image_path", "step_id", "mistake_type", "expected_status"])
        for i in range(6):
            (tmp_path / "images" / f"{i}.png").write_bytes(b"png")
            writer.writerow([f"images/{i}.png", 1 + i % 4, "correct", "pass"])
    return path


def test_load_cases_spreads_the_limit_and_passes_only_path_and_step(manifest):
    cases = load_cases(manifest, CFG, limit=3)
    assert [step.id for _, step in cases] == [1, 3, 1]
    assert all(path.is_file() and not hasattr(path, "mistake_type") for path, _ in cases)


def test_measure_counts_timeouts(manifest):
    cases = load_cases(manifest, CFG, limit=None)
    slow = TimeoutInspector(SlowInspector(MockInspector(CFG), 0.2), 0.05)
    result = measure(slow, cases[:2], warmup=False)
    assert result["timeouts"] == 2 and "unavailable" in result
    fast = measure(MockInspector(CFG), cases)
    assert fast["timeouts"] == 0 and fast["failures"] == 0 and fast["calls"] == 6
    assert 0 <= fast["median_s"] <= fast["p95_s"] <= fast["max_s"]


def test_run_writes_report_reports_na_and_refuses_overwrite(manifest, tmp_path):
    def broken(_cfg):
        raise ConnectionError("ollama not running")
    out = tmp_path / "bench"
    report = run(manifest, out, ["mock", "gemma"], limit=None,
                 factory_map={"mock": MockInspector, "gemma": broken})
    assert report["results"]["mock"]["calls"] == 6
    table = (out / "benchmark.md").read_text()
    assert "| Mock (not AI) |" in table and "| Gemma 3 4B (4-bit) | N/A |" in table
    assert "ollama not running" in table and (out / "benchmark.json").is_file()
    with pytest.raises(FileExistsError):
        run(manifest, out, ["mock"], factory_map={"mock": MockInspector})
    with pytest.raises(ValueError):
        run(manifest, tmp_path / "other", ["nope"], factory_map={"mock": MockInspector})
