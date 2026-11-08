"""VIS-12: latency benchmark for every inspector (thin wrapper).

    python scripts/vis12_latency_benchmark.py outputs/dataset-v1/labels.csv \
        --output outputs/benchmark/vis12-run1 --inspectors cv,gemma,hybrid --limit 40

Same as ``python -m src.vision.latency_benchmark``. Gemma rows need a running
Ollama with gemma3:4b already pulled; otherwise they are reported as N/A.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.vision.latency_benchmark import main  # noqa: E402

if __name__ == "__main__":
    main()
