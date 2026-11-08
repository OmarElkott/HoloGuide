> Integration update — 2026-11-06: This branch now includes current `main` (`866f7c0`), including Partner 2's Gemma inspector and Partner 4's dashboard. The integrated suite passed **166 tests in 13.79 seconds**. References below to an unavailable inspector describe the November 2 implementation stage; inspector source is now present, but real-model accuracy, recovery and GPU measurements are still pending. Generated artifacts remain excluded from Git. See `docs/VISION_MODULE.md` for the current vision interface.

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

Full install check on this Windows/Python 3.12 host selected a PyBullet source
build and failed because Microsoft Visual C++ 14.0+ Build Tools were absent.
The mock-development install below avoids PyBullet. For the full simulator setup,
use the team's Python 3.11 environment with a compatible wheel or install the
required C++ toolchain, then repeat the full install; that path is not verified here.

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
The dashboard defaults to existing control mocks and production Workflow. Place the current part, inspect,
correct faults, or submit an override with an operator identifier and typed reason.
Removing a verified part invalidates its approval through Workflow first.
Each demo session has its own persisted audit under ignored `outputs/dashboard/`.
Reset closes supported environment resources and retains old audit files. The
camera panel displays the current rendered image or an explicit unavailable/mock
placeholder. The last inspected image and step remain distinct from the current
workflow step. The audit viewer supports event filters and JSONL download.
Project Streamlit telemetry is disabled; network availability is not monitored.

`Run scripted mock demo` executes the existing four scenario loops, followed by
low-confidence review, an injected mock TimeoutError, HOLD recovery, reinspection,
and a clearly labelled logged demonstration override. Episodes have separate
workflows and audits. Progress updates automatically; Stop halts between episodes
and reset releases resources. This is not a real-model timeout benchmark or the
final three-minute hardware rehearsal. The scenario transcripts retain the
mistakes, correction messages, reinspection and completion events.

Real mode requires the teammate implementations; it never falls back to mocks.
P1's unmerged `feat/sim-assembly-env` currently supplies `render_to_file(path)` and
`close()`, but `place_part`/`remove_part` are still placeholders. No P2 inspector
is merged. After those dependencies are ready, configure
`HOLOGUIDE_BACKEND_FACTORY=your_team_module:create_dashboard_session`. That factory
must return `DashboardSession(mode='real', environment=env, inspector=inspector,
model=actual_model_name, endpoint=actual_endpoint)`. The dashboard consumes
`render_to_file(path)`, `place_part`, `remove_part`, and `close` only; it never calls
`parts_present()` or passes simulation truth to inspection. The actual control
contract is `inspect(path, StepSpec)`. If P2 implements the roadmap's step-ID
interface, explicitly wrap it with `src.dashboard.backends.StepIdInspectorAdapter`.
No signatures are guessed. Factory construction/cleanup belongs to the integrated
backend. Reset and garbage collection close supported resources; Streamlit has
no reliable immediate browser-disconnect hook, so immediate tab-close cleanup
is not guaranteed. Use reset before leaving a real session.

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
New runs also save `.provenance.json` with manifest/image hashes, configuration,
code revision and dirty state, inspector/model, environment and package versions.
Hardware measurements and model digest must be recorded on the demo laptop.
Accuracy is exact decision-status agreement grouped by ground-truth mistake type.
False-block rate is BLOCK / ground-truth PASS cases (undefined when no eligible
cases). HOLD/review counts and inspection exceptions are reported separately;
failed cases remain in accuracy denominators. Latency measures inspection,
validation, and rule evaluation, excluding artifact writes; p95 uses linear
interpolation. Mock latency is not model latency.

Regenerate tables from existing CSVs without rerunning inspection:

```powershell
.venv/Scripts/python -m src.evaluation.report --result "Mock validation=outputs/evaluation/run-01.csv" --output outputs/evaluation/report-01.md
# When actual corresponding results exist:
.venv/Scripts/python -m src.evaluation.report --result "Gemma only=outputs/evaluation/gemma.csv" --result "OpenCV only=outputs/evaluation/opencv.csv" --result "Hybrid=outputs/evaluation/hybrid.csv" --output outputs/evaluation/comparison.md
```

Unavailable measurements are N/A. Different manifest hashes trigger a comparison
warning. `--recovery path.json` and `--safety path.json` attach actual P1/P3 evidence
without guessing their unagreed output schemas. Legacy CSVs remain readable but
are labelled as lacking provenance. Explicit TimeoutError exceptions are counted
as timeouts; a zero-confidence observation or HOLD alone does not prove a timeout.

Final evaluation remains blocked by P1's dataset/manifest and finished simulation,
P2's configured real inspectors, and the target laptop/model installation. Once
the provisional manifest adapter has been confirmed, the command template is:

```powershell
.venv/Scripts/python -m src.evaluation.runner path/to/confirmed-manifest.csv --inspector-factory your_team_module:create_gemma_inspector --step-id-interface --model gemma3:4b --endpoint http://localhost:11434 --output outputs/evaluation/eval_final.csv
```

Replace the factory with the agreed real factory; omit `--step-id-interface` if
it already accepts StepSpec. Repeat for actual OpenCV/Hybrid inspectors with unique
output paths, then generate the comparison report. No such factories or dataset
are claimed to ship here. All failures remain in results; the runner relies on
P2's bounded timeout implementation and does not create a competing timeout engine.

### Partner 2 inspector factories

The vision module now ships zero-argument factories in `src/vision/factories.py`
(no `--step-id-interface` needed; all accept a StepSpec):

| Factory | Inspector |
|---|---|
| `src.vision.factories:cv` | OpenCV colour masks + shape checks (no GPU, ~15 ms) |
| `src.vision.factories:gemma` | Gemma 3 4B via local Ollama, wrapped in `ai_timeout_s` |
| `src.vision.factories:hybrid` | OpenCV decides; Gemma only settles images OpenCV is unsure about |

```powershell
.venv/Scripts/python -m src.simulation.dataset --output outputs/dataset-v1
.venv/Scripts/python -m src.evaluation.runner outputs/dataset-v1/labels.csv --inspector-factory src.vision.factories:hybrid --model gemma3:4b --endpoint http://localhost:11434 --output outputs/evaluation/hybrid-v1.csv
.venv/Scripts/python -m src.vision.latency_benchmark outputs/dataset-v1/labels.csv --output outputs/benchmark/vis12 --inspectors cv,gemma,hybrid
$env:HOLOGUIDE_BACKEND_FACTORY = 'src.simulation.backend:create_session'
$env:HOLOGUIDE_INSPECTOR_FACTORY = 'src.vision.factories:hybrid'   # dashboard real mode
```

Measured results and the go/no-go choice are in `docs/decisions.md`; module details
in `docs/VISION_MODULE.md`; the writeup section is `docs/WRITEUP_AI_INSPECTION.md`.

The public notebook `notebooks/hologuide_demo.ipynb` is saved with outputs. To re-run it top to bottom
(uses Hybrid if a local Ollama serves `gemma3:4b`, otherwise OpenCV; never downloads a model):

```powershell
.venv/Scripts/python -m pip install nbconvert ipykernel
.venv/Scripts/python -m jupyter nbconvert --to notebook --execute notebooks/hologuide_demo.ipynb --output-dir outputs/notebook-run
```

See `docs/PERSON4_WRITEUP.md` for the writeup, task status, and submission checklist.

## Partner 1 rendered simulation contribution

See [simulation, dataset and recovery documentation](docs/PARTNER1_TECHNICAL.md),
[demo script and recording plan](docs/PARTNER1_DEMO.md), and
[delivery status](docs/PARTNER1_STATUS.md).

![HoloGuide architecture](docs/architecture.png)

### Windows / Python 3.11

Open PowerShell in the repository. Python 3.11 and Git are prerequisites.
The following installation command downloads packages; run it only when network use is authorized.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m src.simulation.dataset --output outputs/dataset-v2 --randomize --seed 2026
.\.venv\Scripts\python.exe -m streamlit run src/dashboard/app.py
```

Using the executable directly avoids PowerShell activation-policy problems. Installation and model provisioning require internet, but rendering and tests use local resources. Provision the chosen real model before disconnecting Wi-Fi. Do not download or pull a model during the offline demo. Follow the real-inspector factory instructions in the technical guide; no Partner 2 inspector currently ships on these integrated branches. The existing mock CLI remains available as `python -m src.control.cli --scenario missing_gasket` and is not AI inference.

Roles: Partner 1 owns simulation, datasets, scenarios, recovery testing and demo preparation; Partner 2 owns vision/model inspection; Partner 3 owns rules, workflow and audit; Partner 4 owns dashboard and evaluation reporting. This contribution extends Partner 4's dashboard/evaluation seams and retains Partner 3's authorization logic.
