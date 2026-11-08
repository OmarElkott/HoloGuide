"""Latency benchmark for every inspector (VIS-12).

Times ``inspector.inspect(image_path, step)`` over images from a dataset
manifest. Only the image path and the StepSpec reach the inspector; the
manifest's labels are never read. One warm-up call per inspector is excluded.

    python -m src.vision.latency_benchmark outputs/dataset-v1/labels.csv \\
        --output outputs/benchmark/vis12-run1 --inspectors cv,gemma,hybrid --limit 40

An inspector that cannot be built, or whose every call fails (e.g. Ollama is
not running), is reported as N/A with the reason instead of a misleading number.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from src.config import AssemblyConfig, load_config
from src.vision import factories
from src.vision.inspector import MockInspector, is_failure, is_timeout
from src.vision.schemas import Observation

FACTORIES: dict[str, Callable[[AssemblyConfig], Any]] = {
    "mock": lambda cfg: MockInspector(cfg),
    "cv": factories.cv,
    "gemma": factories.gemma,
    "hybrid": factories.hybrid,
    "hybrid_explained": factories.hybrid_explained,
}
LABELS = {"mock": "Mock (not AI)", "cv": "OpenCV", "gemma": "Gemma 3 4B (4-bit)",
          "hybrid": "Hybrid", "hybrid_explained": "Hybrid (Gemma explains every image)"}


def load_cases(manifest: Path, config: AssemblyConfig, limit: int | None) -> list[tuple[Path, Any]]:
    """(image path, StepSpec) pairs, spread evenly over the manifest."""
    with manifest.open(encoding="utf-8", newline="") as stream:
        rows = [(manifest.parent / r["image_path"], int(r["step_id"])) for r in csv.DictReader(stream)]
    if not rows:
        raise ValueError("manifest contains no rows")
    if limit and limit < len(rows):
        stride = len(rows) / limit
        rows = [rows[int(i * stride)] for i in range(limit)]
    for path, _ in rows:
        if not path.is_file():
            raise FileNotFoundError(path)
    return [(path, config.step(step_id)) for path, step_id in rows]


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def measure(inspector: Any, cases: list[tuple[Path, Any]], *, warmup: bool = True) -> dict[str, Any]:
    """Time each inspection. Never raises for inspector misbehaviour."""
    if warmup:
        try:
            inspector.inspect(*cases[0])
        except Exception:  # noqa: BLE001 - an inspector should not raise; recorded below
            pass
    durations, timeouts, failures, first_failure = [], 0, 0, ""
    for path, step in cases:
        start = perf_counter()
        try:
            obs = Observation.model_validate(inspector.inspect(path, step).model_dump())
        except Exception as exc:  # noqa: BLE001
            obs = Observation(confidence=0.0, rationale=f"INSPECTION FAILED (raised): {exc}")
        durations.append(perf_counter() - start)
        if is_timeout(obs):
            timeouts += 1
        elif is_failure(obs):
            failures += 1
        if is_failure(obs) and not first_failure:
            first_failure = obs.rationale
    result: dict[str, Any] = {
        "calls": len(durations), "timeouts": timeouts, "failures": failures,
        "median_s": statistics.median(durations), "p95_s": _percentile(durations, 0.95),
        "mean_s": statistics.fmean(durations), "max_s": max(durations),
        "durations_s": durations, "first_failure": first_failure,
    }
    if timeouts + failures == len(durations):
        result["unavailable"] = f"all {len(durations)} calls failed: {first_failure}"
    if hasattr(inspector, "gemma_calls"):
        result["gemma_calls"] = inspector.gemma_calls
    return result


def run(manifest: Path, output: Path, names: list[str], *, limit: int | None = 40,
        config: AssemblyConfig | None = None,
        factory_map: dict[str, Callable[[AssemblyConfig], Any]] | None = None) -> dict[str, Any]:
    config = config or load_config()
    factory_map = factory_map or FACTORIES
    unknown = [n for n in names if n not in factory_map]
    if unknown:
        raise ValueError(f"unknown inspectors {unknown}; choose from {sorted(factory_map)}")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)           # never overwrite a previous run
    cases = load_cases(Path(manifest), config, limit)
    results: dict[str, Any] = {}
    for name in names:
        try:
            inspector = factory_map[name](config)
        except Exception as exc:  # noqa: BLE001
            results[name] = {"unavailable": f"could not build: {type(exc).__name__}: {exc}"}
            continue
        results[name] = measure(inspector, cases)
    report = {"measurement": "wall-clock seconds per inspect() call after one warm-up call",
              "manifest": str(manifest), "images": len(cases), "python": platform.python_version(),
              "platform": platform.platform(), "results": results}
    (output / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "benchmark.md").write_text(markdown_table(report), encoding="utf-8")
    return report


def markdown_table(report: dict[str, Any]) -> str:
    lines = [f"Images: {report['images']} (manifest `{report['manifest']}`), one warm-up call excluded.",
             "", "| Inspector | Median latency (s) | 95th percentile (s) | Timeouts | Other failures |",
             "|---|---:|---:|---:|---:|"]
    notes = []
    for name, r in report["results"].items():
        label = LABELS.get(name, name)
        if "unavailable" in r:
            lines.append(f"| {label} | N/A | N/A | {r.get('timeouts', 'N/A')} | {r.get('failures', 'N/A')} |")
            notes.append(f"- {label}: N/A because {r['unavailable']}")
            continue
        lines.append(f"| {label} | {r['median_s']:.3f} | {r['p95_s']:.3f} | {r['timeouts']} | {r['failures']} |")
        if "gemma_calls" in r:
            notes.append(f"- {label}: Gemma was consulted on {r['gemma_calls']} of "
                         f"{r['calls'] + 1} calls (including warm-up).")
    return "\n".join(lines + ([""] + notes if notes else [])) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="dataset labels.csv (only image_path and step_id are read)")
    parser.add_argument("--output", type=Path, required=True, help="new directory for benchmark.json/.md")
    parser.add_argument("--inspectors", default="cv,gemma,hybrid",
                        help=f"comma-separated, from {','.join(FACTORIES)}")
    parser.add_argument("--limit", type=int, default=40, help="images to time (0 = all)")
    args = parser.parse_args()
    report = run(args.manifest, args.output, [n.strip() for n in args.inspectors.split(",") if n.strip()],
                 limit=args.limit or None)
    print(markdown_table(report))


if __name__ == "__main__":
    main()
