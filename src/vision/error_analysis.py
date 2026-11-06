"""Run an inspector over dataset v1 and write an error analysis (VIS-06).

Reads the images plus the SEPARATE labels file. Labels are used only for
scoring here; they are never given to the inspector.

Expected label fields (one row per image; JSON list, JSONL or CSV):
  image          file name relative to the images folder   (alias: file, path, filename)
  step_id        1-4, the step the image is checked against (alias: step)
  mistake_type   correct | missing | wrong | order | ambiguous
                 (alias: category, label, scenario; e.g. "missing_gasket" works)
  expected_status  optional pass/block/needs_review/not_pass; derived if absent
  parts_present  optional list of parts truly in the image (for per-part recall)
If Partner 1's labels file uses other names, edit ALIASES below, nothing else.
"""

from __future__ import annotations

import csv
import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig
    from ..control.rules import RuleContext, evaluate
    from .inspector import is_failure, is_timeout, resolve_step
    from .schemas import Decision, DecisionStatus, Observation
else:
    from config import AssemblyConfig
    from control.rules import RuleContext, evaluate
    from vision.inspector import is_failure, is_timeout, resolve_step
    from vision.schemas import Decision, DecisionStatus, Observation

ALIASES = {
    "image": ("image", "file", "path", "filename"),
    "step_id": ("step_id", "step"),
    "category": ("mistake_type", "category", "label", "scenario"),
    "expected_status": ("expected_status", "expected"),
    "parts_present": ("parts_present", "parts", "present_parts"),
}
MEMORY_HINTS = ("out of memory", "cuda", "oom", "insufficient memory", "failed to allocate")

# The team's own go/no-go targets from docs/HOLOGUIDE_ROADMAP.md (not industry standards).
A_MIN_ACCURACY, A_MAX_FALSE_BLOCK, A_MAX_MEDIAN_S = 0.85, 0.08, 10.0
C_MAX_ACCURACY, C_MAX_MEDIAN_S = 0.60, 20.0


@dataclass
class LabelRow:
    image: Path
    step_id: int
    category: str
    expected_status: str
    parts_present: list[str] | None = None


@dataclass
class Result:
    row: LabelRow
    observation: Observation
    decision: Decision
    latency_s: float
    inspector_status: str
    matched: bool
    safe: bool
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_category(value: Any) -> str:
    s = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"", "correct", "ok", "good", "none", "pass", "complete"}:
        return "correct"
    if "missing" in s:
        return "missing"
    if "order" in s:
        return "order"
    if "wrong" in s or "decoy" in s:
        return "wrong"
    if "ambig" in s or "occlu" in s:
        return "ambiguous"
    return s


def _pick(raw: dict, key: str) -> Any:
    for name in ALIASES[key]:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def default_expected(category: str) -> str:
    return {"correct": "pass", "ambiguous": "not_pass"}.get(category, "block")


def load_rows(labels_path: Path | str, images_dir: Path | str) -> list[LabelRow]:
    labels_path, images_dir = Path(labels_path), Path(images_dir)
    text = labels_path.read_text(encoding="utf-8")
    if labels_path.suffix == ".csv":
        raw_rows = list(csv.DictReader(text.splitlines()))
    elif labels_path.suffix == ".jsonl":
        raw_rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        data = json.loads(text)
        raw_rows = data["images"] if isinstance(data, dict) and "images" in data else data
    rows = []
    for i, raw in enumerate(raw_rows):
        image, step, cat = _pick(raw, "image"), _pick(raw, "step_id"), _pick(raw, "category")
        if image is None or step is None:
            raise ValueError(f"labels row {i} needs an image and a step id (got keys {sorted(raw)})")
        category = normalize_category(cat)
        parts = _pick(raw, "parts_present")
        if isinstance(parts, str):
            parts = [p.strip() for p in parts.replace(";", ",").split(",") if p.strip()]
        rows.append(LabelRow(images_dir / str(image), int(step), category,
                             str(_pick(raw, "expected_status") or default_expected(category)).lower(),
                             parts))
    return rows


def status_matches(expected: str, status: str) -> bool:
    return status != "pass" if expected == "not_pass" else status == expected


def run_dataset(inspector: Any, config: AssemblyConfig, rows: list[LabelRow],
                progress: Callable[[int, int, Result], None] | None = None) -> list[Result]:
    results = []
    for i, row in enumerate(rows, 1):
        step = resolve_step(config, row.step_id)
        t0 = time.perf_counter()
        observation = inspector.inspect(row.image, step)
        latency = time.perf_counter() - t0
        decision = evaluate(observation, step, config.confidence_threshold,
                            context=RuleContext.from_config(config, step))
        status = decision.status.value
        result = Result(
            row=row, observation=observation, decision=decision, latency_s=latency,
            inspector_status=getattr(inspector, "last_status", "n/a"),
            matched=status_matches(row.expected_status, status),
            safe=(status != "pass") if row.category != "correct" else True,
            extra={"timeout": is_timeout(observation), "failure": is_failure(observation)},
        )
        results.append(result)
        if progress:
            progress(i, len(rows), result)
    return results


def _pct(num: int, den: int) -> float | None:
    return num / den if den else None


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def summarize(results: list[Result], config: AssemblyConfig) -> dict[str, Any]:
    by_cat: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_cat[r.row.category].append(r)

    categories = {}
    for cat, items in sorted(by_cat.items()):
        statuses = Counter(r.decision.status.value for r in items)
        categories[cat] = {
            "n": len(items),
            "accuracy": _pct(sum(r.matched for r in items), len(items)),
            "false_accepts": statuses.get("pass", 0) if cat != "correct" else 0,
            "statuses": dict(statuses),
        }
    correct = by_cat.get("correct", [])
    false_block = _pct(sum(r.decision.status is DecisionStatus.BLOCK for r in correct), len(correct))
    correct_not_pass = _pct(sum(r.decision.status is not DecisionStatus.PASS for r in correct), len(correct))

    parts: dict[str, Counter] = defaultdict(Counter)
    swaps: Counter = Counter()
    for r in results:
        truth = r.row.parts_present
        if truth is None or r.extra["failure"]:
            continue
        seen, real = set(r.observation.parts_detected), set(truth)
        for name in {p.name for p in config.parts}:
            parts[name]["tp"] += name in seen and name in real
            parts[name]["fp"] += name in seen and name not in real
            parts[name]["fn"] += name not in seen and name in real
        for fp in seen - real:
            for fn in real - seen:
                swaps[(fn, fp)] += 1
    part_stats = {}
    for name, c in sorted(parts.items()):
        part_stats[name] = {"tp": c["tp"], "fp": c["fp"], "fn": c["fn"],
                            "precision": _pct(c["tp"], c["tp"] + c["fp"]),
                            "recall": _pct(c["tp"], c["tp"] + c["fn"])}

    latencies = [r.latency_s for r in results if not r.extra["failure"]] or [r.latency_s for r in results]
    steady = latencies[1:] or latencies
    details = " ".join(r.observation.rationale.lower() for r in results if r.extra["failure"])
    summary = {
        "n": len(results),
        "categories": categories,
        "false_block_rate": false_block,
        "review_or_hold_on_correct": correct_not_pass,
        "false_accepts_total": sum(c["false_accepts"] for c in categories.values()),
        "parts": part_stats,
        "part_swaps": [{"truth": a, "reported": b, "count": n} for (a, b), n in swaps.most_common(8)],
        "inspector_failures": sum(r.extra["failure"] for r in results),
        "timeouts": sum(r.extra["timeout"] for r in results),
        "memory_errors": sum(h in details for h in MEMORY_HINTS),
        "latency": {"first_s": results[0].latency_s if results else None,
                    "median_s": statistics.median(steady) if steady else None,
                    "p95_s": _p95(steady) if steady else None,
                    "max_s": max(latencies) if latencies else None},
    }
    summary["go_no_go"] = go_no_go(summary)
    return summary


def go_no_go(summary: dict[str, Any]) -> dict[str, str]:
    cats = summary["categories"]
    accs = [cats[c]["accuracy"] for c in ("missing", "wrong") if c in cats and cats[c]["accuracy"] is not None]
    if not accs:
        return {"option": "?", "reason": "No missing-part or wrong-part images in the dataset yet."}
    worst, fb = min(accs), summary["false_block_rate"] or 0.0
    median = summary["latency"]["median_s"] or 0.0
    if worst < C_MAX_ACCURACY or median > C_MAX_MEDIAN_S or summary["memory_errors"]:
        return {"option": "C", "reason": f"OpenCV primary: worst missing/wrong accuracy {worst:.0%}, "
                f"median latency {median:.1f}s, memory errors {summary['memory_errors']}."}
    if worst >= A_MIN_ACCURACY and fb <= A_MAX_FALSE_BLOCK and median <= A_MAX_MEDIAN_S \
            and summary["false_accepts_total"] == 0:
        return {"option": "A", "reason": f"Gemma primary: worst missing/wrong accuracy {worst:.0%}, "
                f"false-block {fb:.0%}, median latency {median:.1f}s, no false accepts."}
    return {"option": "B", "reason": f"Hybrid: worst missing/wrong accuracy {worst:.0%}, false-block {fb:.0%}, "
            f"median latency {median:.1f}s, false accepts {summary['false_accepts_total']}."}


def write_csv(results: list[Result], path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["image", "step_id", "category", "expected_status", "decision", "matched", "latency_s",
                    "inspector_status", "confidence", "detected", "missing", "wrong", "rationale", "message"])
        for r in results:
            o = r.observation
            w.writerow([r.row.image.name, r.row.step_id, r.row.category, r.row.expected_status,
                        r.decision.status.value, int(r.matched), f"{r.latency_s:.2f}", r.inspector_status,
                        o.confidence, ";".join(o.parts_detected), ";".join(o.missing_parts),
                        ";".join(o.wrong_parts), o.rationale, r.decision.message])


def _fmt(x: float | None, pct: bool = True) -> str:
    return "n/a" if x is None else (f"{x:.0%}" if pct else f"{x:.1f}")


def render_markdown(summary: dict[str, Any], results: list[Result], meta: dict[str, Any]) -> str:
    g = summary["go_no_go"]
    lat = summary["latency"]
    out = [f"# Gemma error analysis (VIS-06)", "",
           f"- Model: `{meta.get('model', 'n/a')}`  |  prompt version: `{meta.get('prompt_version', 'n/a')}`  |  images: {summary['n']}",
           f"- Run: {meta.get('when', '')}", "",
           f"## Suggested option: **{g['option']}**", "", g["reason"], "",
           "Thresholds are the team's own targets: A needs worst missing/wrong accuracy >= 85%, "
           "false-block <= 8%, median latency <= 10 s (and, here, zero false accepts); C applies below 60%, "
           "above 20 s, or on memory errors; otherwise B. Treat this as input to the team meeting, not a verdict.", "",
           "## Accuracy by mistake type", "",
           "| Type | Images | Decision matches expected | False accepts | Decisions |", "|---|---|---|---|---|"]
    for cat, c in summary["categories"].items():
        out.append(f"| {cat} | {c['n']} | {_fmt(c['accuracy'])} | {c['false_accepts']} | {c['statuses']} |")
    out += ["", f"False-block rate on correct images: **{_fmt(summary['false_block_rate'])}**; "
            f"sent to review or hold instead of pass: {_fmt(summary['review_or_hold_on_correct'])}.", "",
            "## Latency and failures", "",
            f"- First image {_fmt(lat['first_s'], False)} s (may include model load), steady median "
            f"{_fmt(lat['median_s'], False)} s, p95 {_fmt(lat['p95_s'], False)} s, max {_fmt(lat['max_s'], False)} s.",
            f"- Inspector failures: {summary['inspector_failures']} (timeouts {summary['timeouts']}, "
            f"memory errors {summary['memory_errors']})."]
    if summary["parts"]:
        out += ["", "## Per-part detection", "", "| Part | Precision | Recall | TP | FP | FN |", "|---|---|---|---|---|---|"]
        for name, p in summary["parts"].items():
            out.append(f"| {name} | {_fmt(p['precision'])} | {_fmt(p['recall'])} | {p['tp']} | {p['fp']} | {p['fn']} |")
        if summary["part_swaps"]:
            out += ["", "Most common confusions (truth -> reported): " +
                    ", ".join(f"{s['truth']} -> {s['reported']} x{s['count']}" for s in summary["part_swaps"])]
    bad = sorted((r for r in results if not r.matched),
                 key=lambda r: (r.row.category == "correct", r.decision.status is not DecisionStatus.PASS))
    out += ["", "## Worst failures (look at these images first)", ""]
    if not bad:
        out.append("None.")
    for r in bad[:15]:
        out.append(f"- `{r.row.image.name}` step {r.row.step_id} [{r.row.category}] expected "
                   f"{r.row.expected_status}, got **{r.decision.status.value}**: {r.decision.message} "
                   f"(Gemma: \"{r.observation.rationale}\")")
    return "\n".join(out) + "\n"
