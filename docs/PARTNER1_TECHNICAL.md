# Partner 1: simulation and evaluation setup

HoloGuide renders a static assembly in PyBullet DIRECT mode with TinyRenderer. Four assembly steps add a grey base plate, orange gasket, blue cover and four red bolts. A green decoy is a wrong cover. The gasket is wider than the cover and the bolts sit outside its footprint, preserving visible evidence. Components are static bodies; this is an assembly inspection prototype, not a validated dynamics or manufacturing simulator.

## Architecture and trust boundary

![Architecture](architecture.png)

The simulation sends an RGB PNG to the inspector. The inspector returns a validated Observation. Partner 3's rules and Workflow alone authorize advancement; the runner never writes the current step and never overrides a failed decision. The dashboard renders that state and its audit trail. Dataset CSV labels and simulator part state are evaluation ground truth and never inspector arguments. The existing mock CLI intentionally reads JSON fixtures; it must not be mistaken for the rendered-image pipeline.

## Dataset protocol

`python -m src.simulation.dataset --output outputs/dataset-v1 --seed 2026`

`python -m src.simulation.dataset --output outputs/dataset-v2 --seed 2026 --randomize`

Each command generates 200 PNGs: 80 correct, 40 missing-part, 40 wrong-part and 40 visible-future-part cases. Correct and missing cases span all four steps; wrong-part cases introduce the green decoy, replacing the cover when present. Wrong-order cases span steps 1–3 because step 4 has no future part. A still image cannot establish historical action order. Report this category as visible out-of-order parts, separately from the action guard.

The labels.csv fields include image_path (relative to the CSV), step_id, mistake_type, expected_status, seed, parts_present and SHA-256. This directly satisfies Partner 4's evaluation runner. Images use numeric filenames and contain no labels or metadata with the answer. dataset.json records configuration and seed. Output directories must be new; partial failed runs remain available for diagnosis and must not be treated as complete datasets.

V1 is a fixed-scene regression dataset: repetitions are intentional and do not constitute independent visual diversity. V2 changes yaw ±3 degrees, pitch ±2 degrees, light direction and component XY offsets ±3 mm, using a local NumPy generator. Reset repeats the seed. Determinism is checked within the installed PyBullet/platform stack; byte equality across versions or machines is not promised. Do not split duplicate V1 frames across tuning and evaluation. For independent V2 evaluation, use a disjoint seed range and compare image hashes.

## Scenario and recovery protocol

`python -m src.simulation.runner --scenario missing_gasket --output outputs/scenario-001 --inspector-factory your_module:create_inspector`

Replace `your_module:create_inspector` with Partner 2's installed factory. It takes no arguments and returns an inspector. The production workflow calls inspect(path, StepSpec). Add `--step-id-interface` for the roadmap inspect(path, step_id) contract. No inspector implementation is inferred or silently substituted.

The four scenarios use a fresh environment and workflow. Correct verifies each of four steps. Missing gasket verifies the base, physically places a premature cover, requires BLOCK, removes the cover, inserts the gasket and continues. Wrong cover verifies base and gasket, injects the decoy, requires BLOCK, replaces it and continues. Wrong order first checks the guard without invoking inspection, then explicitly injects the cover to test the visual path; after BLOCK it removes the cover and continues. These physical injections are deliberate test operations, logged separately from authorized placement. Fault cases stop on unexpected PASS, HOLD or Needs Review; they never force completion. Playback is bounded and advances one inspection per tick.

`python -m src.evaluation.recovery --output outputs/recovery-real-001 --inspector-factory your_module:create_inspector --runs 20 --seed 50000 --evidence real-inspector`

Run this only with the actual inspector. It produces 80 trials, including 20 correct controls and 20 of each fault. Recovery means an observed BLOCK followed by correction and PASS; completion is recorded separately. recovery_s measures from the blocking decision to the correction pass, including correction, rendering and inspection. It is not model-only latency. All attempts, failures, seeds, decisions and per-trial audits are retained. Evidence classification is a caller declaration: verify the factory and provenance before publishing any results.

For image-level evaluation reuse Partner 4:

`python -m src.evaluation.runner outputs/dataset-v2/labels.csv --output outputs/eval-real.csv --inspector-factory your_module:create_inspector`

Without a real factory that command defaults to the existing JSON mock, which cannot inspect PNGs. Always specify the factory for these datasets.

## Dashboard integration

In PowerShell:

```powershell
$env:HOLOGUIDE_BACKEND_FACTORY = 'src.simulation.backend:create_session'
$env:HOLOGUIDE_INSPECTOR_FACTORY = 'your_module:create_inspector'
# Only if Partner 2 implements inspect(path, step_id):
$env:HOLOGUIDE_STEP_ID_INTERFACE = '1'
python -m streamlit run src/dashboard/app.py
```

Select Real and start/reset the session. Load a scenario before placing any parts, then play each step. The launcher uses the same runner as CLI/evaluation. Start a fresh session for another scenario. Existing mock Demo Mode remains explicitly mock and is separate from real scenario playback. Model/endpoint display can be configured with HOLOGUIDE_MODEL_NAME and HOLOGUIDE_MODEL_ENDPOINT; these labels do not monitor network traffic.

## Optional fault and visuals

`env.offset_part('cover', 0.05, 0)` injects a misalignment without changing its identity; all bodies belonging to a component move together. This is a geometry capability, not an implemented vision alignment detector. Presence-only inspectors may pass it. Use it as a qualitative demo only until Partner 2 supplies alignment evidence and the team agrees on labels/rules. Existing configured colors, unobstructed gasket rim, external bolt positions and neutral workstation are preserved. Cosmetic text must remain outside inspection PNGs; the dashboard already labels steps and parts.

## Limits and remaining validation

No Partner 2 real inspector is present in the inspected branches. Scripted test observations verify orchestration, rendering, stopping behavior and audit integration only. They do not establish AI accuracy, GPU latency, real recovery success or offline network behavior. Real-model evaluation, GUI rehearsal and physical recording remain pending. Real results must replace placeholders only after measured runs on the demo laptop. Never publish the scripted recovery CSV as competition performance.
