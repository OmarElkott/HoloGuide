# HoloGuide: Day 1 Summary

Day 1 built the foundation. No finished features exist yet. What exists is the shared structure that all three partners will build on from Day 2 onward.

## 1. What HoloGuide is

HoloGuide is an offline AI assembly-inspection assistant for a virtual manufacturing workflow. A simulated camera looks at a virtual assembly, a local AI model (Gemma 3 4B) reports what it sees, and fixed workflow rules decide whether the process may continue.

## 2. The core design rule

> **The AI reports. The rules decide.**

Everything in the project follows this data flow:

```text
AssemblyEnv.render()  ->  Inspector.inspect(image, step)  ->  Observation
  ->  Workflow.decide(observation)  ->  Decision  ->  Dashboard / Audit log
```

- The **AI** only produces an `Observation` about one image.
- The **control layer** alone produces a `Decision` and advances the step.
- The AI sees only the rendered image, never the simulator's internal "correct answer". This keeps HoloGuide a real inspection system rather than a scripted demo.

## 3. What was built on Day 1

| Item | Location | Purpose |
|---|---|---|
| Assembly definition | `config/assembly.yaml` | One source of truth for parts, steps, camera, and thresholds |
| Config loader | `src/config.py` | Loads and validates the YAML; catches typos early |
| Data contracts | `src/vision/schemas.py` | Defines `Observation` and `Decision`, the shared language between modules |
| Scene render script | `scripts/day1_render_scene.py` | Proves PyBullet runs and produces images of the assembly |
| Gemma smoke test | `scripts/day1_ollama_smoke_test.py` | Proves Gemma 3 4B runs on a 6GB GPU and measures its speed |
| Unit tests | `tests/test_config.py`, `tests/test_schemas.py` | 7 tests protecting the config and schemas |
| Project setup | `.gitignore`, `pytest.ini`, `requirements.txt`, `__init__.py` files | Keeps the repo clean and imports working |

## 4. Each part explained

### `config/assembly.yaml`
Defines the virtual assembly:
- **Parts:** base plate (grey), gasket (orange), cover (blue), bolts (red), plus a green `wrong_cover` decoy.
- **Steps:** (1) place base plate, (2) insert gasket, (3) attach cover, (4) install bolts.
- **Settings:** camera position, `confidence_threshold` (0.70), and `ai_timeout_s` (20).

**Why it matters:** nobody hard-codes part names, step order, or thresholds in Python. Changing the assembly means editing this one file.

**How the three mistake types map to it:**
- *Missing part:* a step where a required part is absent.
- *Wrong part:* the green `wrong_cover` appears instead of the blue `cover`.
- *Incorrect order:* a step is attempted before an earlier one is done.

### `src/config.py`
Loads the YAML and validates it with Pydantic. It rejects duplicate part names, steps that reference parts that don't exist, and step ids that aren't consecutive.

Its `expected_parts(step_id)` method returns every part that should be visible once a step is complete. For example, `expected_parts(3)` returns base plate, gasket, and cover. The rules engine (Day 8) uses this to judge the AI's observation.

### `src/vision/schemas.py`
This file defines the two contracts that connect the whole system.

- **`Observation`** is what the AI may say: `parts_detected`, `missing_parts`, `wrong_parts`, `confidence` (0 to 1), and `rationale`.
- **`Decision`** is what the rules produce: `step_id`, `status`, `message`, `decided_by`, the original `observation`, and a `timestamp`.
- **`DecisionStatus`** has four values: `pass`, `block`, `needs_review`, and `hold`.
- **`DecidedBy`** has three values: `rule`, `operator`, and `system`.
- **`uncertain_observation()`** returns a safe, zero-confidence result when the AI fails or its output can't be parsed.

**Why it matters:** the `Observation` JSON schema is also passed to Ollama, which forces Gemma's output into exactly this shape. Keeping `Observation` and `Decision` separate enforces "AI reports, rules decide" in the code itself.

### `scripts/day1_render_scene.py`
Builds the assembly in headless PyBullet (no window) and saves three PNGs to `outputs/`:
1. `scene_complete.png`: everything assembled correctly.
2. `scene_missing_gasket.png`: the gasket is absent.
3. `scene_wrong_cover.png`: the green cover replaces the blue one.

The gasket is deliberately wider than the cover, so an orange rim stays visible from above. Parts hidden under other parts are very hard for an AI to verify.

**Why it matters:** it proves the simulation produces usable camera images. On Days 2 and 3 this script becomes the `AssemblyEnv` class.

### `scripts/day1_ollama_smoke_test.py`
Sends each rendered image to `gemma3:4b` through Ollama, validates the reply as an `Observation`, and prints the response time.

**Why it matters:** it finds out on Day 1, not Day 6, whether the model is usable and fast enough on a 6GB GPU. The prompt includes the expected parts for the step but never the ground-truth label of the image.

### Tests
- `test_config.py` checks that the config loads with four steps, that `expected_parts` accumulates correctly, and that an unknown part is rejected.
- `test_schemas.py` checks that a valid observation parses, that out-of-range confidence is rejected, that the uncertain fallback has zero confidence, and that `Decision` defaults to `rule`.

### Setup files
- `.gitignore` keeps virtual environments, caches, model files, videos, and `outputs/` out of Git.
- `pytest.ini` puts `src/` on the import path.
- `requirements.txt` lists the packages. It deliberately leaves out `torch` and `transformers`, because Gemma runs through Ollama and fits in 6GB VRAM.

## 5. How Day 1 serves the project

| Day 1 work | What it enables later |
|---|---|
| `Observation` and `Decision` schemas | All three partners can work in parallel against fixed interfaces |
| `assembly.yaml` and `config.py` | The rules engine knows what to expect at each step; the simulator knows what to draw |
| Rendered scenes | Partner 1 builds `AssemblyEnv`; Partner 2 builds the evaluation dataset |
| Gemma smoke test | An early read on speed and accuracy, which feeds the Day 6 go/no-go decision |
| Tests | Safe changes: a broken config or schema fails immediately |

## 6. How to run and verify

```bash
python -m venv .venv                      # then activate it
pip install -r requirements.txt
pytest                                    # expect 7 passed
python scripts/day1_render_scene.py       # writes 3 PNGs to outputs/
ollama pull gemma3:4b                     # Ollama 0.6 or later
python scripts/day1_ollama_smoke_test.py  # prints JSON and timing
```

Python 3.10 or 3.11 is recommended, because PyBullet wheels may be missing on newer versions.

## 7. Day 1 results (fill in)

- pytest: ___ passed
- Images render and look distinct: yes / no
- Gemma returns valid JSON: ___ of 3 images
- Gemma latency after warm-up: ~___ seconds per image
- Gemma noticed the missing gasket: yes / no
- Gemma noticed the green cover: yes / no
- Known issues: ___

## 8. Next steps

| Partner | Day 2 task |
|---|---|
| Partner 1 (simulation) | Turn the render script into `src/simulation/` (`scene.py`, `camera.py`) and work toward `AssemblyEnv` with `reset()`, `place_part()`, `remove_part()`, and `render()` |
| Partner 2 (vision) | Write `MockInspector` in `src/vision/inspector.py`, returning canned `Observation` objects, then evolve the smoke test into the real `Inspector` |
| Partner 3 (control and dashboard) | Start `src/control/` (workflow state machine and rules) against `MockInspector`, using `cfg.expected_parts()` and `DecisionStatus` |

**Team rule:** don't change the fields of `Observation` or `Decision` without telling the group. All three modules depend on them.

## 9. Not done yet (on purpose)

- No `AssemblyEnv` class (Days 2-3)
- No real inspector with prompts, retry, and fallback (Days 3-4)
- No evaluation dataset or accuracy numbers (Days 5-6)
- No workflow or rules engine (Days 7-8)
- No dashboard, override, or audit log (Days 11-13)
