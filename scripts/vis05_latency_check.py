"""VIS-05: model warm-up plus a VRAM and first-call latency check.

    python scripts/vis05_latency_check.py                 # uses outputs/scene_complete.png
    python scripts/vis05_latency_check.py --cold --runs 8

--cold unloads the model first so you see the true cold-start cost.
"""
import argparse, json, shutil, statistics, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import load_config
from vision.gemma_inspector import GemmaInspector


def gpu_memory():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
        used, total = (int(x) for x in out.split(","))
        return {"used_mib": used, "total_mib": total}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", type=Path, default=ROOT / "outputs" / "scene_complete.png")
    ap.add_argument("--step", type=int, default=4)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--cold", action="store_true")
    args = ap.parse_args()
    if not args.image.is_file():
        sys.exit(f"Image not found: {args.image}\nRun: python scripts/day1_render_scene.py")

    cfg = load_config()
    insp = GemmaInspector(cfg, model=args.model, request_timeout_s=120)
    report = {"model": args.model, "gpu_before": gpu_memory()}
    if args.cold:
        insp.unload(); time.sleep(2)
    warm = insp.warm_up()
    print(f"warm-up: {warm['seconds']:.1f}s ok={warm['ok']} {warm['error'] or ''}")
    if not warm["ok"]:
        sys.exit("Warm-up failed. Is Ollama running and the model pulled? (ollama pull gemma3:4b)")
    times = []
    for i in range(args.runs):
        obs = insp.inspect(args.image, args.step)
        times.append(insp.last_latency_s)
        print(f"run {i + 1}: {times[-1]:.1f}s status={insp.last_status} conf={obs.confidence} detected={obs.parts_detected}")
    mem = insp.memory_report()
    report.update(warm_up_s=warm["seconds"], first_call_s=times[0], steady_median_s=statistics.median(times[1:] or times),
                  max_s=max(times), memory=mem, gpu_after=gpu_memory())
    print(json.dumps(report, indent=2))
    frac = mem.get("gpu_fraction")
    if frac is not None and frac < 0.99:
        print(f"WARNING: only {frac:.0%} of the model is in VRAM; the rest runs on CPU (slow). "
              "Close GPU apps or lower num_ctx / image size.")
    med = report["steady_median_s"]
    print("Latency verdict:", "OK for option A (<=10 s)" if med <= 10 else
          "Hybrid zone (10-20 s)" if med <= 20 else "Too slow for Gemma-primary (>20 s)")
    out = ROOT / "outputs" / "vis05_latency.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
