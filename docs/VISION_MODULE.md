# HoloGuide - Vision / AI Inspection (Partner 2) - Handoff

> **Update (2026-11-13):** Gemma now does measurable work. `ForeignObjectIdentifier` (`foreign_objects.py`) names objects OpenCV can only localise, `GemmaFirstInspector` lets Gemma read every frame with OpenCV as a safety net, and `outage.py` simulates an unreachable Gemma. Notes below about Gemma never being consulted, and the test count, describe Nov 7. Current results: [`decisions.md`](decisions.md) (the "Foreign objects and Gemma-first" and "Live line monitor" sections).

**Tasks:** VIS-01 to VIS-12 and DOC-04a/04b (notebook), all done. Results and the go/no-go decision are in [`decisions.md`](decisions.md).
**Core rule:** the AI reports, the rules decide. An inspector never raises, never sees ground truth, and never changes workflow state.

## What each file does

| File | Task | Purpose |
|---|---|---|
| `src/vision/prompts.py` | VIS-01, VIS-10 | Prompt v1 (step-aware, part names) and **v2** (step-agnostic true/false colour questions, the default). JSON schemas for both |
| `src/vision/gemma_inspector.py` | VIS-02, 03, 05, 10 | Ollama call, validation into `Observation`, one retry, safe fallback, `warm_up()`, `memory_report()`, optional `crop`, `prompt_version` |
| `src/vision/inspector.py` | VIS-04 | `InspectorABC`, `MockInspector`, `SlowInspector`, `TimeoutInspector` |
| `src/vision/cv_inspector.py` | VIS-07a, 07b, 11, 08 | OpenCV inspector: HSV masks derived from `assembly.yaml` colours, pixel share → confidence, bolt blob count, box-shape check for the covers, unrecognised-object check |
| `src/vision/hybrid_inspector.py` | VIS-09 | OpenCV decides presence; Gemma only settles the parts OpenCV is unsure about |
| `src/vision/factories.py` | integration | Zero-argument `cv`, `gemma`, `hybrid`, `hybrid_explained` factories for the runners and the dashboard |
| `src/vision/latency_benchmark.py`, `scripts/vis12_latency_benchmark.py` | VIS-12 | Median / p95 / timeouts for each inspector; N/A when an inspector is unavailable |
| `scripts/vis05_latency_check.py` | VIS-05 | Warm-up, first-call vs steady latency, VRAM fraction |
| `src/vision/error_analysis.py`, `scripts/vis06_run_gemma.py` | VIS-06 | Error analysis plus an A/B/C suggestion |
| `notebooks/hologuide_demo.ipynb` | DOC-04a, 04b | Public notebook (saved with outputs): inspect one image, full scenario, audit log, Needs Review/override and Hold edge cases, a 20-image evaluation, full results |
| `tests/test_cv_inspector.py`, `test_hybrid_inspector.py`, `test_vision_factories.py`, `test_vis12_benchmark.py`, `test_vision_prompts.py`, `test_gemma_inspector.py`, `test_inspector_timeout.py`, `test_error_analysis.py` | all | No Ollama needed. The OpenCV tests render real PyBullet frames |

## How an image is inspected

```
OpenCV:  image -> HSV mask per part -> pixel share (or blob count / box shape)
         -> present | absent | ambiguous per part -> Observation
Gemma:   image + v2 prompt (no step, no part names) -> {dark_grey_plate: bool, orange_layer: bool, ...,
         certainty, rationale} -> code maps colours to parts -> Observation
Hybrid:  OpenCV sure  -> OpenCV Observation (Gemma not called)
         OpenCV unsure -> Gemma settles only the ambiguous parts; disagreement, an unsure Gemma,
                          an unrecognised object or a Gemma failure -> confidence 0.5 -> Needs Review
then:    Observation -> control.rules.evaluate -> Decision
```

- **OpenCV confidence:** each part scores 0.75-0.95 when it is clearly present or absent (more margin gives a higher score). An ambiguous part scores 0.50, below the 0.70 threshold, so the step goes to operator review. Wrong bolt counts (1-3), a green or blue region that isn't box-shaped, and more than 1.5% of the image unexplained by any part are all treated as ambiguous.
- **Gemma confidence:** the certainty words clear / partly_clear / unclear map to 0.90 / 0.60 / 0.30. `missing_parts` is filled only when certainty is clear.
- **Failures:** any failure (unreadable image, bad JSON twice, connection error, timeout) returns confidence **0.0**, which the rules turn into **HOLD**. `vision.inspector.is_timeout(obs)` tells a timeout apart from other failures.

## Usage

```bash
# Evaluation, scenarios, dashboard: all take a module:function factory (StepSpec interface, no adapter)
.venv/bin/python -m src.evaluation.runner outputs/dataset-v2/labels.csv --inspector-factory src.vision.factories:hybrid \
    --model gemma3:4b --endpoint http://localhost:11434 --output outputs/evaluation/hybrid-new.csv
.venv/bin/python -m src.simulation.runner --scenario missing_gasket --output outputs/runs/demo \
    --inspector-factory src.vision.factories:hybrid
HOLOGUIDE_BACKEND_FACTORY=src.simulation.backend:create_session \
HOLOGUIDE_INSPECTOR_FACTORY=src.vision.factories:hybrid .venv/bin/python -m streamlit run src/dashboard/app.py
.venv/bin/python -m src.vision.latency_benchmark outputs/dataset-v2/labels.csv --output outputs/benchmark/new \
    --inspectors cv,gemma,hybrid --limit 40
```

| Environment variable | Default | Effect |
|---|---|---|
| `HOLOGUIDE_MODEL_NAME` | `gemma3:4b` | Ollama model (must already be pulled; nothing is downloaded) |
| `HOLOGUIDE_MODEL_ENDPOINT` | Ollama default (localhost:11434) | Ollama host |
| `HOLOGUIDE_PROMPT_VERSION` | `v2` | `v1` reproduces the run-1 baseline |

```python
from src.config import load_config
from src.vision import factories

cfg = load_config()
inspector = factories.hybrid(cfg)
obs = inspector.inspect("outputs/frame.png", cfg.step(3))   # a StepSpec or a step id
```

Before a demo, warm Gemma up so the first call isn't slow: `factories.gemma(cfg).inner.warm_up()`.

## Decisions to confirm with the team

1. **Inspector signature:** `inspect(image_path, step)` accepts a `StepSpec` (what `workflow.py` passes) **or** an int. The roadmap text says `step_id`.
2. **Hybrid is the demo inspector (option B).** On these renders it makes the same decisions as OpenCV, because Gemma was never consulted; see `decisions.md`.
3. **Not a vision issue (Partner 3):** the `missing_gasket` scenario message is "Incorrect assembly order", not "Gasket missing", because the rules check parts from later steps first.

## Verification record

```text
Date/time:       2026-11-07, dev laptop (RTX 3060 Laptop 6 GB, Ollama, gemma3:4b 2.9 GB 100% GPU)
Branch:          main (code at f110a1d; docs in the following commit)
python -m pytest: 213 passed
Gemma v2 / held-out v2: missing 100%, wrong part 100%, wrong order 85%, false-block 7.5%, median 3.2 s
OpenCV and Hybrid v1+v2: 100% on every mistake type, false-block 0%, median <= 0.02 s
VIS-12 (40 images): Gemma 3.019 s median / 3.272 s p95; OpenCV 0.015 / 0.020; Hybrid 0.019 / 0.021; 0 timeouts
Recovery (OpenCV, 20 runs per scenario): 20/20 recovered for missing_gasket, wrong_cover, wrong_order
Suggested option: B (Hybrid)
Notebook:        jupyter nbconvert --execute -> 10/10 code cells, 0 errors (Hybrid inspector)
Open issues:     Gemma misses the orange gasket on a bare plate under randomised lighting (decisions.md)
```
