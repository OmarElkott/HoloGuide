# HoloGuide - Vision decisions (Partner 2)

Go/no-go record (VIS-06) and results of VIS-07 to VIS-12. Every number below comes from a real run on
the dev laptop (RTX 3060 Laptop 6 GB, Ollama, `gemma3:4b` 4-bit, 2.9 GB fully on the GPU), measured on
2026-11-07. Artifacts are under `outputs/` (gitignored); the commands to regenerate them are at the end.
These are results on **synthetic PyBullet renders**, not evidence of physical-world performance.

## Decision: option B (Hybrid) is the default inspector

| Option | Roadmap rule | Measured |
|---|---|---|
| A: Gemma primary | missing and wrong-part accuracy >= 85%, false-block <= 8%, median <= 10 s | Gemma with prompt v2 on the held-out dataset v2 scores 100% / 100%, with 7.5% false blocks and a 3.2 s median. That is inside A, but only just, and it **accepts 15% of wrong-order images** |
| B: Hybrid | accuracy 60-85% or latency 10-20 s | Hybrid: 100% on every mistake type, 0% false blocks, 0.02 s median |
| C: OpenCV primary | accuracy < 60%, latency > 20 s or VRAM errors | OpenCV alone: 100% on every type, 0% false blocks, 0.014 s median |

**Chosen: B. `HybridInspector` (`src.vision.factories:hybrid`) is the inspector for the dashboard and the demo.**
- **How the hybrid works:** OpenCV decides which parts are present. Gemma is asked only when OpenCV is unsure, and its answer is used only for the parts OpenCV was unsure about. If Gemma disagrees with OpenCV, is unsure itself, or fails, the step goes to operator review. That route never ends in a pass.
- **Why not Gemma alone:** it fails in the unsafe direction (false accepts) on one scene type (see VIS-10 below), and it is about 200x slower.
- **Why not OpenCV alone:** on this data the Hybrid and OpenCV make the same decisions, and the Hybrid keeps a model-based second opinion for images OpenCV cannot settle. Gemma was consulted on 0 of 400 dataset images and 0 of 41 benchmark calls, because OpenCV was never unsure on these clean renders.
- **Update 2026-11-13:** Gemma now does measurable work in the hybrid: it identifies foreign objects that OpenCV can only localise. See "Foreign objects and Gemma-first" below.
- **Caveat:** Hybrid numbers here are effectively OpenCV numbers. The Gemma path is covered by unit tests (`tests/test_hybrid_inspector.py`), not by these datasets.

## Results by inspector

The datasets come from `src.simulation.dataset`. Each has 200 images: 80 correct, plus 40 each of missing part, wrong part and wrong order. Images in v1 are fixed; v2 is generated with `--randomize --seed 2026` (camera, lighting and part offsets vary). "Accuracy" means the rules engine's decision matched the label (`src.evaluation.runner`).

| Inspector | Dataset | Correct | Missing part | Wrong part | Wrong order | False-block rate | Median s | p95 s |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| OpenCV | v1 | 100% | 100% | 100% | 100% | 0.0% | 0.014 | 0.016 |
| OpenCV | v2 | 100% | 100% | 100% | 100% | 0.0% | 0.014 | 0.017 |
| Gemma, prompt v1 (run 1) | v1 | 100% | 25% | 50% | 100% | 0.0% | 1.455 | 2.313 |
| Gemma, prompt v1 (run 1) | v2 | 98.75% | 25% | 25% | 97.5% | 1.25% | 2.405 | 2.675 |
| Gemma, prompt v2 (run 2) | v1 | 100% | 100% | 100% | 100% | 0.0% | 1.973 | 2.126 |
| Gemma, prompt v2 (run 2) | v2 (held out) | 92.5% | 100% | 100% | 85% | 7.5% | 3.245 | 3.618 |
| Hybrid | v1 | 100% | 100% | 100% | 100% | 0.0% | 0.020 | 0.022 |
| Hybrid | v2 | 100% | 100% | 100% | 100% | 0.0% | 0.018 | 0.022 |

Timeouts were 0 and inspection errors were 0 in every run. Latency is measured end to end: inspection, validation and rule evaluation.

## VIS-12 latency benchmark

Run with `src.vision.latency_benchmark` on 40 images spread across dataset v2. One warm-up call is excluded from the timings.

| Inspector | Median latency (s) | 95th percentile (s) | Timeouts |
|---|---:|---:|---:|
| Gemma 3 4B (4-bit) | 3.019 | 3.272 | 0 |
| OpenCV | 0.015 | 0.020 | 0 |
| Hybrid | 0.019 | 0.021 | 0 |

## VIS-10: what run 1 showed and what changed

**Run 1 failure:** prompt v1 told Gemma which step was being checked and where to look, and the model reported what *should* be there:
- an empty table → "The grey base plate is the largest, lowest piece";
- the green decoy at step 3 → reported as "cover";
- missing bolts at step 4 → "all four bolts".

All of these were **false accepts**: the step passed while a mistake was present. Missing-part accuracy was only 25%.

**Fix, prompt v2** (`src/vision/prompts.py`, the default for the factories):
- Step-agnostic: the step is never shown to the model.
- One true/false question per colour. The answers are keyed by colour (`dark_grey_plate`, `orange_layer`, `blue_square`, `green_square`, `red_pegs`), never by part name, and code maps them back to parts using `assembly.yaml`.
- The wording was checked on 15 dataset-v1 images before the full run. A colour-list variant was rejected because the model copied the whole list for an empty table. The base-plate question needed a rewording to stop the model missing the plate.

**Crop:** `GemmaInspector(crop=True)` crops to the assembly using the image's own pixels. In the same 15-image check it gave identical answers to no crop, so it was **not enabled**.

**Result:**
- On v1, Gemma went from 25% to 100% on missing parts and from 50% to 100% on wrong parts.
- On the held-out v2, all 12 remaining errors come from one scene: the orange gasket on the plate with nothing on top, under randomised lighting, where Gemma does not see the gasket. That produced 6 false blocks (correct, step 2) and 6 false accepts (wrong order, step 1).
- No further tuning was done against v2, to keep it a fair held-out set.

`HOLOGUIDE_PROMPT_VERSION=v1` reproduces the run-1 baseline.

## Recovery trials (EVL-03 tool, OpenCV inspector)

`src.evaluation.recovery`, 20 runs per scenario, evidence `real-inspector`:

| Scenario | Runs | Recovered | Errors |
|---|---:|---:|---:|
| correct | 20 | n/a (completed 20) | 0 |
| missing_gasket | 20 | 20 | 0 |
| wrong_cover | 20 | 20 | 0 |
| wrong_order | 20 | 20 | 0 |

## Foreign objects and Gemma-first (added 2026-11-13)

**Why.** On datasets v1 and v2 OpenCV was never unsure, so the hybrid consulted Gemma on 0 of 400 images and the
VLM did no measurable work. A foreign object left on the assembly (a tool, rag or debris: FOD) is a real
shop-floor escape that neither model handles alone:
- OpenCV notices pixels that belong to no known part, but it cannot say what they are. Before this change it
  also ignored anything under 1.5% of the image, so a screwdriver lying on the cover (about 0.13%) **passed**.
- In a probe of 6 tool and rag scenes, Gemma 3 4B on the full 640x480 frame answered "no other object" for all 6.
  Asked to list every object, it named only 2 of them.

**Design: OpenCV says where, Gemma says what, the rules decide.**
- `CVSettings.max_unexplained` is now 0.0005, down from 0.015. Clean renders measure exactly 0.0 unexplained on all
  200 dataset-v2 images, and the smallest object covers about 0.13%.
- When OpenCV finds unexplained pixels, `ForeignObjectIdentifier` (`src/vision/foreign_objects.py`) shows Gemma a
  336x336 crop of that region only. Gemma answers with a kind (tool, cloth, debris, assembly_part or unclear) and a name.
- If the kind is tool, cloth or debris, the name goes into `Observation.foreign_objects`. That is a new optional
  field in the shared schema, defaulting to empty.
- Any other answer, or a failure or timeout, caps confidence below the threshold, so the step goes to operator review.
- The rules check foreign objects **first**, giving BLOCK: "Foreign object in the work area: \<name\>. Remove it and
  inspect … again". A foreign object can hide a part or be mistaken for one, so the rest of that frame is
  unreliable. In a real-Gemma scenario run, the Gemma-first inspector misread a purple screwdriver as bolts.
  The rules had told the operator "incorrect order" until this check was moved first.
- Gemma only ever sees the crop. Labels and `foreign_present()` stay in the simulator and the runner.

**Dataset v3** (held out, never used for tuning): `--randomize --seed 3030 --correct 40 --per-fault 10 --foreign 60`.
That is 130 images. Each foreign-object case is a correct assembly at steps 1-4, plus a screwdriver, a wrench or a
rag, alternately on the assembly and beside it on the table. Expected status is BLOCK.

| Inspector | Dataset | Correct | Missing | Wrong part | Wrong order | Foreign object | False-block | Reviews | Faults passed | Median s | p95 s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OpenCV | v3 | 100% | 100% | 100% | 100% | 0% (60 → review) | 0.0% | 60 | 0 | 0.016 | 0.026 |
| Gemma 3 4B alone | v3 | 85% | 100% | 100% | 60% | 46.7% | 15.0% | 0 | **36** | 2.824 | 3.095 |
| **Hybrid + identifier** | v3 | 100% | 100% | 100% | 100% | **100%** | 0.0% | 0 | 0 | 0.024 | 2.208 |
| Gemma-first + identifier | v3 | 85% | 90% | 100% | 60% | **100%** | 0.0% | 11 | 0 | 3.013 | 4.983 |
| Gemma-first | v2 | 92.5% | 90% | 100% | 85% | n/a | 0.0% | 16 | 0 | 2.930 | 4.297 |
| OpenCV (regression) | v2 | 100% | 100% | 100% | 100% | n/a | 0.0% | 0 | 0 | 0.014 | 0.016 |
| Hybrid (regression) | v2 | 100% | 100% | 100% | 100% | n/a | 0.0% | 0 | 0 | 0.016 | 0.019 |

"Faults passed" counts mistake images that got PASS, which is the unsafe direction. "Reviews" counts NEEDS_REVIEW.
Accuracy is an exact match with the label, so a review on a mistake image counts as wrong even though it is safe.
Timeouts and errors were 0 in every run. The v2 regression runs show that the lower threshold changed nothing
on clean images.

**Reading the results.**
- Gemma alone passed 32 of 60 foreign-object scenes and 4 of 10 wrong-order images. On its own it is not safe.
- Gemma-first turns every Gemma disagreement into a review. It passed no mistake image on v2 or v3, at the cost of
  8–11% of images going to an operator. Compared with Gemma alone on v2, its 6 false accepts and 6 false blocks all
  became reviews.
- Hybrid + identifier is the only configuration that is right on every v3 case. Gemma now does real work there:
  it was consulted on 60 of 130 images, about 2 s each, and only on the foreign-object frames.
- **Naming is a hint, not ground truth.** Gemma classified all 60 regions as foreign (tool 41, debris 19, never
  "assembly_part"). Its name matched the real object in only 28 of 60: screwdriver 14/20, rag 9/20, wrench 5/20.
  Wrenches were often called "screwdriver" or "hammer". The BLOCK depends only on the kind.
- Not tested: a region OpenCV flags that really is part of the assembly (for example a glare or shadow artefact on
  a real camera). Gemma never answered "assembly_part" here, so that path, which leads to operator review, is
  covered only by unit tests.
- The v3 CSVs for Gemma-first were written before foreign objects moved to the top of the rule order. A frame with
  a foreign object was BLOCK under both orders, so the statuses above are unchanged. Only the wording of some
  messages would differ.

**Demo scenario.** `foreign_object` is in `scenarios.ALL_NAMES` and the dashboard's rendered scenario launcher.
The step-3 cover is placed correctly, then a screwdriver is left on it. That gives BLOCK, naming the screwdriver.
The screwdriver is removed, the step is re-inspected, and it passes. Run with real Gemma, both `hybrid` and
`gemma_first` completed and recovered. The block message was "Foreign object in the work area: purple screwdriver
(tool)".

## Known limitations

- The renders are clean and synthetic. OpenCV's 100% depends on the distinct part colours and the fixed, shadow-free lighting. Its thresholds (`CVSettings`) were checked on both datasets: present parts cover at least 0.37% of the image and absent parts 0.00%. A real camera would need recalibration.
- Gemma's held-out errors make it unsuitable as the only inspector. Its role is tie-breaker and explainer (`factories:hybrid_explained` adds a Gemma sentence to every result as text only).
- Not vision, for Partner 3: the rules check parts belonging to a later step before missing parts. So in the `missing_gasket` scenario, which places the cover during step 2, the message is "Incorrect assembly order: Cover belongs to a later step" rather than the demo script's "Gasket missing".

## Reproduce

```bash
.venv/bin/python -m src.simulation.dataset --output outputs/dataset-v1 --seed 0
.venv/bin/python -m src.simulation.dataset --output outputs/dataset-v2 --randomize --seed 2026
# per inspector (cv | gemma | hybrid) and dataset (v1 | v2); outputs must be new paths
.venv/bin/python -m src.evaluation.runner outputs/dataset-v2/labels.csv --inspector-factory src.vision.factories:gemma \
    --model gemma3:4b --endpoint http://localhost:11434 --output outputs/evaluation/gemma-v2-run2.csv
HOLOGUIDE_PROMPT_VERSION=v1 .venv/bin/python -m src.evaluation.runner ...   # run-1 baseline prompt
.venv/bin/python -m src.vision.latency_benchmark outputs/dataset-v2/labels.csv --output outputs/benchmark/vis12-run2 \
    --inspectors cv,gemma,hybrid --limit 40
.venv/bin/python -m src.evaluation.recovery --output outputs/recovery/cv-run1 \
    --inspector-factory src.vision.factories:cv --evidence real-inspector
.venv/bin/python -m src.simulation.dataset --output outputs/dataset-v3 --randomize --seed 3030 \
    --correct 40 --per-fault 10 --foreign 60
.venv/bin/python -m src.evaluation.runner outputs/dataset-v3/labels.csv --inspector-factory src.vision.factories:hybrid \
    --model gemma3:4b --output outputs/evaluation/hybrid-v3-run1.csv     # also cv, gemma, gemma_first
.venv/bin/python -m src.simulation.runner --scenario foreign_object --inspector-factory src.vision.factories:hybrid \
    --output outputs/runs/fod-demo
```

Runs 1 and 2 of Gemma used the code at `f110a1d` and its working-tree predecessor. Prompt v2 is the only change between them.
