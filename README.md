# HoloGuide
A locally powered AI assembly system that verifies manufacturing steps and catches defects

## Core idea
- AI observes the virtual assembly through simulated camera images.
- Fixed rules validate each step and prevent incorrect progression.
- The dashboard explains missing or incorrect parts and confirms corrections.
- The operator can inspect system decisions and override when appropriate.

## Scope
- One virtual assembly
- Four assembly steps
- Three mistake types: missing part, wrong part, incorrect order
- Local AI inspection
- Dashboard-based operator workflow

## Team roles
- Simulation and assembly interaction
- AI inspection and image testing
- Workflow rules, dashboard, and integration

## Development and tests

Use Python 3.11 or 3.12. From the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
```

For control, evaluation and mock dashboard development without PyBullet/Ollama:

```powershell
.venv/Scripts/python -m pip install pytest pydantic pyyaml streamlit
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m streamlit run src/dashboard/app.py
```

Tests use temporary files. Shared Person 4 fixtures live in `tests/conftest.py`.
The dashboard uses the existing control mocks and production Workflow; it does
not provide real camera/AI integration yet. Place the current part, inspect,
correct faults, or submit an override with an operator identifier and typed reason.
Removing a verified part invalidates its approval through Workflow first.
Each demo session has its own persisted audit under ignored `outputs/dashboard/`.
New session retains the old audit files. The scene display is a list of visible
parts, not a camera image. The audit viewer supports event filters and JSONL download.
Project Streamlit telemetry is disabled; network availability is not monitored.

## Evaluation

No labelled evaluation dataset or real Inspector ships in this checkout.
Do not treat mock test outcomes as model performance. Until the dataset owner
supplies its manifest contract, the runner supports this **provisional adapter**:

```csv
image_path,step_id,mistake_type,expected_status
```

Paths are relative to the manifest; step IDs reference `config/assembly.yaml`.
Use actual case labels for `mistake_type`; expected status is `pass`, `block`,
`needs_review`, or `hold`. Each case is evaluated independently, not as a sequential
workflow. No data rows are supplied here because the dataset is unavailable.
Only image path and StepSpec are passed to the inspector; scoring labels stay in
the runner. The inspector implementation must also avoid reading label metadata.

```powershell
.venv/Scripts/python -m src.evaluation.runner path/to/manifest.csv --output outputs/evaluation/run-01.csv
```

The CLI uses the existing `control.cli.MockInspector`, which requires mock JSON
snapshots produced by `MockAssemblyEnvironment`; it does not accept camera PNGs.
For a real implementation call `src.evaluation.runner.run(manifest, inspector,
config, output)` with an object implementing `inspect(image_path, step)`.
The runner refuses to overwrite existing artifacts. It writes per-case CSV,
`.summary.json`, and `.tables.md` beside the selected output in ignored `outputs/`.
Accuracy is exact decision-status agreement grouped by ground-truth mistake type.
False-block rate is BLOCK / ground-truth PASS cases (undefined when no eligible
cases). HOLD/review counts and inspection exceptions are reported separately;
failed cases remain in accuracy denominators. Latency measures inspection,
validation, and rule evaluation, excluding artifact writes; p95 uses linear
interpolation. Mock latency is not model latency.

See `docs/PERSON4_WRITEUP.md` for the writeup, task status, and submission checklist.
