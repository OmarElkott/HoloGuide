# HoloGuide - Vision / AI Inspection (Partner 2) - Handoff

**Tasks:** VIS-01, VIS-02, VIS-03, VIS-05, VIS-06, VIS-04
**Core rule:** the AI reports, the rules decide. The inspector never raises, never sees ground truth and never changes workflow state.

## What each file does

| File | Task | Purpose |
|---|---|---|
| `src/vision/prompts.py` | VIS-01 | Per-step prompt built from `assembly.yaml` + a JSON schema that limits part names to the catalog |
| `src/vision/gemma_inspector.py` | VIS-02, 03, 05 | Ollama call, validation into `Observation`, one retry, safe fallback, `warm_up()`, `memory_report()` |
| `src/vision/inspector.py` | VIS-04 | `InspectorABC`, `MockInspector`, `SlowInspector`, `TimeoutInspector` |
| `scripts/vis05_latency_check.py` | VIS-05 | Warm-up, first-call vs steady latency, VRAM fraction, `nvidia-smi` |
| `src/vision/error_analysis.py` + `scripts/vis06_run_gemma.py` | VIS-06 | Run Gemma on dataset v1, write CSV + markdown error analysis + A/B/C suggestion |
| `tests/test_vision_prompts.py`, `test_gemma_inspector.py`, `test_inspector_timeout.py`, `test_error_analysis.py` | all | 32 tests using a fake Ollama client |

## How an image is inspected

```
image + step -> prompt (no expected parts) -> Gemma (schema-constrained JSON)
  -> {parts_detected, certainty, rationale}
  -> code derives: confidence, wrong_parts, missing_parts (only when certainty = clear)
  -> Observation -> control.rules.evaluate -> Decision
```

- `certainty` clear / partly_clear / unclear maps to confidence 0.90 / 0.60 / 0.30. The YAML threshold is 0.70, so only "clear" can pass.
- Any failure (bad JSON twice, connection error, missing image, timeout) returns confidence **0.0**, which `rules.evaluate` maps to **HOLD**.
- A timeout is marked by the rationale prefix `TIMEOUT:`; use `vision.inspector.is_timeout(obs)` to tell it from other failures.

## Usage

```python
from config import load_config
from vision.gemma_inspector import make_default_inspector   # Gemma + ai_timeout_s wrapper

cfg = load_config()
inspector = make_default_inspector(cfg)
inspector.inner.warm_up()                                    # do this before the demo
obs = inspector.inspect("outputs/frame.png", cfg.step(3))   # a StepSpec or a step id both work
```

## Commands

```bash
python -m pytest                                  # all tests, no Ollama needed
python scripts/vis05_latency_check.py --cold      # VIS-05 (needs Ollama + outputs/scene_complete.png)
python scripts/vis06_run_gemma.py --images-dir <dir> --labels <file> --dry-run   # check the labels format
python scripts/vis06_run_gemma.py --images-dir <dir> --labels <file>             # real run (VIS-06)
```

## Decisions to confirm with the team

1. `inspect(image_path, step)` accepts a `StepSpec` (what `workflow.py` passes) **or** an int. The roadmap text says `step_id`.
2. Confidence is derived from certainty words, not from the model's own number.
3. Labels-file field names must match Partner 1's dataset (`ALIASES` in `error_analysis.py`).
4. The CLI real-mode adapter is not wired (Partner 3 owns it).

## Verification record (fill in after running on the laptop)

```text
Date/time:
Branch:
python -m pytest:            ___ passed
vis05 warm-up / first call / steady median / VRAM fraction:
vis06 images / worst missing-wrong accuracy / false-block / false accepts:
Suggested option (A/B/C):
Open issues:
```
