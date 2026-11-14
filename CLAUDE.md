# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

HoloGuide is a local AI assembly verifier built as a four-person course project. A PyBullet scene renders a virtual "Sealed Cover Assembly": 4 steps (base plate → gasket → cover → bolts) and 3 mistake types (missing part, wrong part, incorrect order). Gemma 3 4B, served by Ollama, inspects the rendered images. Deterministic rules decide whether a step passes, and a Streamlit dashboard runs the operator workflow. Ownership: Partner 1 owns simulation/dataset/demo, Partner 2 owns vision, Partner 3 owns rules/workflow/audit, and Partner 4 owns dashboard/evaluation. The plan and task IDs (e.g. VIS-02) are in `HOLOGUIDE_ROADMAP.md`. Per-partner handoff docs are in `docs/` and `CONTROL_MODULE_PROGRESS.md`.

## Commands

The README targets Windows (`.venv/Scripts/python`). On this Linux machine the venv is `.venv/bin/python` (Python 3.10).

```bash
.venv/bin/python -m pytest -q                                   # full suite (~12s, no Ollama/GPU needed)
.venv/bin/python -m pytest -q tests/test_rules.py               # one file
.venv/bin/python -m pytest -q tests/test_workflow.py::test_name # one test
.venv/bin/python -m streamlit run src/dashboard/app.py          # dashboard (mock backend by default)
.venv/bin/python -m src.control.cli --scenario missing_gasket   # mock CLI scenario (not AI inference)
.venv/bin/python -m src.simulation.dataset --output outputs/dataset-v2 --randomize --seed 2026
.venv/bin/python -m src.evaluation.runner path/to/manifest.csv --output outputs/evaluation/run-01.csv
.venv/bin/python -m src.evaluation.report --result "Label=outputs/evaluation/run-01.csv" --output outputs/evaluation/report.md
```

Other entry points with argparse CLIs: `src.simulation.runner` (requires `--inspector-factory module:function`), `src.simulation.rehearsal`, `src.simulation.benchmark`, `src.simulation.export_video`, and the `scripts/vis0*` Gemma latency/error-analysis scripts. The scripts need a running Ollama with the model already pulled.

There is no linter or formatter configured.

Known issue: tests/test_dashboard.py::test_streamlit_startup fails with the installed Streamlit 1.65 because AppTest no longer exposes a progress attribute. The dashboard still uses st.progress; the test must use the supported AppTest API instead.

## Architecture

Core invariant: **the AI reports, the rules decide.** Data moves through layers that never skip each other:

```
environment.render_to_file(path) -> PNG
  -> inspector.inspect(image_path, StepSpec) -> Observation   (src/vision; never raises, never sees ground truth)
  -> control.rules.evaluate(obs, step, threshold, context=RuleContext) -> Decision   (pure function)
  -> control.workflow.Workflow (state machine) + AuditLog (JSONL)
  -> dashboard / evaluation runner
```

- **`config/assembly.yaml` + `src/config.py`**: the single source of parts, steps, camera, `confidence_threshold` (0.70) and `ai_timeout_s` (20). Pydantic validates it: steps must be numbered 1..N consecutively, and every `adds_parts` entry must name a known part. `wrong_cover` is the decoy part (`is_decoy: true`).
- **`src/vision/schemas.py`**: shared contracts. `Observation` (inspector output) and `Decision`/`DecisionStatus` (`pass`/`block`/`needs_review`/`hold`). Only the control layer creates a `Decision`. Changing these schemas affects all four partners.
- **Vision (`src/vision/`)**: `GemmaInspector` asks the model for `parts_detected` plus a `certainty` level. The code then derives confidence (clear/partly_clear/unclear → 0.90/0.60/0.30), `wrong_parts` (detected decoys), and `missing_parts` (only when certainty is "clear"). Any failure returns an Observation with confidence 0.0, which the rules map to HOLD. Timeouts are flagged by the rationale prefix `TIMEOUT:` (`is_timeout()`). `TimeoutInspector` wraps any inspector with `ai_timeout_s`. `MockInspector`/`SlowInspector` are used in tests.
- **Rules (`src/control/rules.py`)**: checks run in a fixed priority order: future-step parts (incorrect order) → wrong/decoy/unknown parts → missing parts → confidence threshold. Pass `RuleContext.from_config(...)` to get the cumulative and decoy checks.
- **Workflow (`src/control/workflow.py`)**: states `waiting/inspecting/blocked/needs_review/hold/step_complete/complete`. Callers must call `check_action(part)` before placing a part (the order guard). Operator overrides go through `operator_override(approve, reason, operator)` and require a typed reason. `clear_hold`, `invalidate_part` (removing a verified part revokes its approval) and the retry policy are also handled here. Every transition is written to the audit log.
- **Simulation (`src/simulation/`)**: `AssemblyEnv` is the PyBullet environment (`render_to_file`, `place_part`, `remove_part`, `close`). `parts_present()` is simulation ground truth and must never reach an inspector. `scenarios.py` defines the scripted mistake scenarios, `runner.ScenarioRunner` drives them, and `dataset.py` writes labelled images and a manifest.
- **Dashboard (`src/dashboard/`)**: `DashboardSession` connects environment + inspector + Workflow + per-session audit in `outputs/dashboard/`. The default is mock mode (`control.cli.MockAssemblyEnvironment`). Real mode is chosen by environment variable and **never falls back to mocks**: `HOLOGUIDE_BACKEND_FACTORY=module:function` must return `DashboardSession(mode='real', ...)`. `src/simulation/backend.py:create_session` is the built-in factory and reads `HOLOGUIDE_INSPECTOR_FACTORY`, `HOLOGUIDE_STEP_ID_INTERFACE=1`, `HOLOGUIDE_MODEL_NAME`, and `HOLOGUIDE_MODEL_ENDPOINT`. `StepIdInspectorAdapter` adapts inspectors that take `inspect(path, step_id)` instead of a `StepSpec`.
- **Evaluation (`src/evaluation/`)**: `runner.run(manifest, inspector, config, output)` evaluates each manifest row (`image_path,step_id,mistake_type,expected_status`) independently. Only the image path and the StepSpec reach the inspector; labels stay in the runner. The runner refuses to overwrite artifacts. It writes CSV + `.summary.json` + `.tables.md` + `.provenance.json`. `report.py` rebuilds comparison tables from existing CSVs.

### Import convention (gotcha)

`pytest.ini` sets `pythonpath = src`, so two import styles coexist. Older control/vision modules and their tests use bare imports (`from control.workflow import ...`, `from vision.schemas import ...`). Newer code uses `from src.x import ...`. Shared modules handle both styles with an `if __package__ and __package__.startswith("src."):` relative-import branch, and new modules in `control/`, `vision/` or `config` should keep that pattern. Python treats `vision.schemas.Observation` and `src.vision.schemas.Observation` as **different classes**, so within one code path, avoid mixing the two import styles for types that are checked with isinstance or used as enums.

## Conventions

- Generated files (`outputs/`, `runs/`, models, `*.mp4`) are gitignored. Don't commit them.
- Mock results are not model performance. Report unavailable measurements as N/A rather than estimating them.
- PRs target `main` (there is no `dev` branch) and follow `.github/pull_request_template.md`: about 300 changed lines, list the validation commands with their actual results, and confirm that labels stay separate from inspector inputs and that overrides go through Workflow.
- The offline demo must not download or pull models. Provision the Ollama model beforehand.

## Working rules

- Before changing code, identify the roadmap task ID and state the goal.
- Make the smallest change that satisfies the task.
- Do not change shared contracts (`src/vision/schemas.py`, `config/assembly.yaml`) without explicitly explaining the impact on all partners.
- Never let simulator ground truth reach the Inspector.
- Never let the AI advance the workflow; only the rules/workflow layer may do that.
- Run the relevant pytest files before claiming a task is complete.
- Do not commit generated files under `outputs/` or `runs/`.

## Hackathon context

- **Competition:** Build with AI - Smart Automation, Robotics, and Vision (GDG Windsor Community Hackathon; sponsors GDG Windsor and DataRealm)
- **Kaggle link:** https://www.kaggle.com/competitions/build-with-ai-smart-automation-robotics-and-vision
- **Timeline:** started 2026-10-07; **final submission deadline Fri 2026-11-13 23:59:59 ET (EST, UTC-5)**. Kaggle has no separate Timeline tab; the dates come from the Overview page.
- **In-person requirement:** local competition. Only participants who can answer questions about their submission in person in Windsor, Ontario on **Fri 2026-11-20** are eligible.
- **Prizes:** DevFest 2026 Track, $1,000 CAD total (1st $500, 2nd $300, 3rd $200). Winners grant the sponsor a non-exclusive license.
- **Theme:** pair deterministic logic (behaviour trees, state machines, rules) with VLMs (Gemma, Gemini Robotics) for judgement. Offline/on-device solutions earn extra credit. HoloGuide fits Part B idea 6 (camera-based quality and part inspection) and idea 9 (assembly assistant).

### Submission format

A valid submission is a **Kaggle Writeup** that includes all of the following:
- Media Gallery
- Attached Public Notebook
- Attached Public Video
- Attached Project Link

Draft or unsubmitted Writeups at the deadline are not judged. Each submission enters exactly one track.

### Team and submission limits

- Maximum team size is **5**. The team leader can merge teams before the merge deadline. Submissions made before a merge are unsubmitted.
- **One submission per team.**
- One Kaggle account per person. Code may not be shared privately outside the team. Public sharing is allowed via Kaggle forums or notebooks. Open-source dependencies must use OSI-approved licenses that allow commercial use. External data, models, and tools must be reasonably accessible to everyone at minimal cost.

### Evaluation (judged rubric, 100 points)

Judges are a panel from GDG Windsor, DataRealm, and community partners.

1. **Architecture: Deterministic Logic & AI Handoffs (30 pts)**
   - Clear separation of concerns: deterministic logic handles what must never fail (motion, safety stops, hard alert thresholds). AI handles open-ended perception, diagnostics, and judgement.
   - Verifiability and override: operators can inspect, verify, and override the system. Handoffs and handbacks between the AI and the control routines are explicit and safe.
2. **Shop-Floor Deployment: On-Device, Multi-Node & Hybrid Design (25 pts)**
   - Fully offline or single-device setups earn top marks. Multi-device local topologies are also valid.
   - Hybrid local/cloud setups can score well only if they justify what stays local and what is offloaded, and show safe handling of latency and connectivity drops.
3. **Industrial Impact & Technical Execution (25 pts)**
   - Real-world viability: solves a real bottleneck in manufacturing, tooling, inspection, logistics, or agri-tech.
   - Depth of implementation: clean, modular, functional code. For standalone or Part B tools, judges assess the robustness of the vision/log/sensor pipeline, latency performance, and day-to-day usability for a shop-floor worker.
4. **Writeup, Demo & Architectural Justification (20 pts)**
   - The Kaggle Writeup and Notebook clearly explain the problem, the architecture, and the exact data and control flow.
   - Justification of trade-offs: why these models, this hardware topology, and this control structure.
   - The video shows the system working end to end, including how it handles edge cases, faults, and unexpected inputs.
