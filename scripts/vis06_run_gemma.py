"""VIS-06: run Gemma on dataset v1 and write an error analysis for the go/no-go.

    python scripts/vis06_run_gemma.py --images-dir outputs/dataset_v1 --labels outputs/dataset_v1/labels.jsonl
    python scripts/vis06_run_gemma.py ... --dry-run     # MockInspector, checks the labels format only

Writes outputs/vis06_results.csv and outputs/vis06_error_analysis.md. Paste the
markdown into docs/decisions.md for the team meeting.
"""
import argparse, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import load_config
from vision.error_analysis import load_rows, render_markdown, run_dataset, summarize, write_csv
from vision.gemma_inspector import DEFAULT_MODEL, GemmaInspector
from vision.inspector import MockInspector, TimeoutInspector
from vision.prompts import PROMPT_VERSION


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "outputs")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=None, help="seconds; default ai_timeout_s from the YAML")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-image-side", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    rows = load_rows(args.labels, args.images_dir)[: args.limit]
    missing = [r.image for r in rows if not r.image.is_file()]
    if missing:
        sys.exit(f"{len(missing)} images listed in the labels file do not exist, e.g. {missing[0]}")

    if args.dry_run:
        inspector = MockInspector(cfg)
    else:
        timeout = args.timeout or cfg.ai_timeout_s
        gemma = GemmaInspector(cfg, model=args.model, request_timeout_s=timeout, max_image_side=args.max_image_side)
        warm = gemma.warm_up()
        print(f"warm-up {warm['seconds']:.1f}s ok={warm['ok']} {warm['error'] or ''}")
        if not warm["ok"]:
            sys.exit("Warm-up failed. Start Ollama and run: ollama pull " + args.model)
        inspector = TimeoutInspector(gemma, timeout)

    def progress(i, n, r):
        print(f"[{i}/{n}] {r.row.image.name} {r.row.category:<9} -> {r.decision.status.value:<12} "
              f"{'OK ' if r.matched else 'MISS'} {r.latency_s:.1f}s")

    results = run_dataset(inspector, cfg, rows, progress)
    summary = summarize(results, cfg)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(results, args.out_dir / "vis06_results.csv")
    meta = {"model": "MockInspector (dry run)" if args.dry_run else args.model,
            "prompt_version": PROMPT_VERSION, "when": time.strftime("%Y-%m-%d %H:%M")}
    report = render_markdown(summary, results, meta)
    (args.out_dir / "vis06_error_analysis.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
