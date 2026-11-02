# HoloGuide — Control Module Progress & Handoff

**Area:** Partner 3 — Control: workflow, rules, override, audit  
**Working branch:** `feat/control`  
**Last updated:** October 28, 2026  
**Status:** Four planned Codex implementation phases requested; local code and test results **not independently verified in this document**.

> **Purpose:** A working handoff for the team. This records the control architecture, the tasks attempted, the acceptance criteria to check before merging, and the remaining integration work. Update the checklist and test results after reviewing the actual code; don't treat tasks as complete solely because Codex ran a prompt.

## 1. Project overview

HoloGuide is a **local/offline virtual assembly inspection prototype**. A PyBullet simulation provides camera images; an inspector (Gemma 3 via Ollama, OpenCV, or a hybrid) produces a structured `Observation`; deterministic control logic decides whether assembly can continue; a Streamlit dashboard displays decisions and lets an operator intervene.

**Core principle: AI reports. Rules decide.** The AI is evidence, not an authority to change workflow state. Simulator ground-truth labels must not be provided to the inspector as if they were camera observations.

### Assembly scope

The assembly and thresholds come from `config/assembly.yaml`, not hardcoded control constants:

1. Place base plate.
2. Insert gasket.
3. Attach cover.
4. Install bolts.

Primary fault types: **missing part**, **wrong part** (including green `wrong_cover`), and **incorrect order**. The documented confidence threshold is **0.70**, configurable in YAML.

## 2. Ownership and interfaces

| Area | Owner | Key location / contract |
|---|---|---|
| Simulation and scenarios | Partner 1 | `src/simulation/`, `AssemblyEnv` |
| Vision and AI inspection | Partner 2 | `src/vision/inspector.py`, `inspect(image_path, step_id) -> Observation` |
| **Deterministic control** | **Partner 3 (this work)** | `src/control/`, rules, workflow, order guard, override, audit, CLI |
| Dashboard and evaluation | Partner 4 | `src/dashboard/`, `src/eval/` |

Shared dependencies already described in the repository:

- `src/config.py`: `AssemblyConfig`, `StepSpec`, `load_config()`, `expected_parts(step_id)`.
- `src/vision/schemas.py`: `Observation`, `Decision`, `DecisionStatus`, `DecidedBy`.
- `DecisionStatus`: `pass`, `block`, `needs_review`, `hold`.
- `DecidedBy`: `rule`, `operator`, `system`.

**Contract rule:** Consult the actual code before changing method signatures. Do not change shared `schemas.py` without discussing it with the other teammates. In particular, the roadmap's `evaluate(observation, step, threshold)` signature does not itself supply the cumulative expected parts; confirm how the actual implementation resolves this.

## 3. Control architecture

```text
Operator action
    |
    v
Order guard: validate the attempted part and sequence (before AI)
    |
    +---- rejected ----> BLOCK / correction message / audit
    |
    v
Simulation action -> render image -> Inspector.inspect(image, step)
                                       |
                                       v
                                 Observation
                                       |
                                       v
                           Deterministic rules engine
                                       |
                                       v
                                Decision
                                       |
                                       v
                              Workflow state
                                       |
                     +-----------------+------------------+
                     |                 |                  |
                    PASS              BLOCK         NEEDS_REVIEW / HOLD
                     |                 |                  |
                next step         stay on step       no advancement
                     |
                  COMPLETE

All meaningful actions, decisions, and overrides -> audit JSONL
Workflow status + decisions -> teammate's dashboard
```

**Workflow states:** `WAITING`, `INSPECTING`, `BLOCKED`, `NEEDS_REVIEW`, `HOLD`, `STEP_COMPLETE`, `COMPLETE`.

Only the workflow module is permitted to change the current step. No failed, uncertain, or unavailable inspection can independently authorize progression. Manual approval is a separate, explicitly logged operator action.

## 4. Work undertaken on October 28

Four detailed Codex prompts were run locally, in order. The list below describes **the implementation each prompt requested**, not independently confirmed results. Before marking complete, inspect changed files and run the full test suite.

### Phase 1 — Rules engine and state machine

**Roadmap IDs:** CTL-01, CTL-02, CTL-03, CTL-04 (initial coverage)

- [ ] Verify `src/control/rules.py` implements deterministic `Observation` → `Decision` evaluation.
- [ ] Verify cumulative expected-part checking uses configuration, not fixed part names.
- [ ] Verify missing/wrong parts block and unclear evidence cannot pass.
- [ ] Verify threshold handling for low-confidence observations.
- [ ] Verify helpful messages for passes, missing/wrong parts, uncertainty, and failures.
- [ ] Verify `src/control/workflow.py` tracks legal states, enforces step order, and completes after the final step.
- [ ] Verify tests cover normal, failed, uncertain, contradictory, and terminal-state scenarios.

### Phase 2 — Order guard and audit logging

**Roadmap IDs:** CTL-05, CTL-08, CTL-11a

- [ ] Verify `Workflow.check_action(part)` checks order **before** simulator placement or AI calls.
- [ ] Verify unknown/decoy/future-step actions are rejected safely.
- [ ] Verify changing previously approved parts invalidates relevant approvals or requires reinspection.
- [ ] Verify `src/control/audit_log.py` appends timestamped JSON objects to `outputs/audit.jsonl`.
- [ ] Verify records contain enough context for the dashboard: step, event type, decision, actor, reason, and observation where relevant.
- [ ] Verify audit write failures are handled safely.
- [ ] Verify order-guard and audit unit tests.

### Phase 3 — Override, Hold recovery, and safety invariants

**Roadmap IDs:** CTL-06, CTL-07, CTL-09, EVL-04

- [ ] Verify manual approve/reject requires a nonempty **operator** and **reason**.
- [ ] Verify approval is marked `decided_by=operator` and durably audited before any corresponding advancement.
- [ ] Verify Hold is entered for identifiable inspection failure/timeout and does not advance the step.
- [ ] Verify clearing Hold requires a reason, is audited, and does **not** count as a pass.
- [ ] Verify failed-check retry escalation works without bypassing Hold or manual review.
- [ ] Verify seeded randomized safety tests exercise at least 500 sequences and maintain the no-unauthorized-advancement invariant.

### Phase 4 — CLI and integration tests

**Roadmap IDs:** CTL-10, CTL-11b, CTL-14 (initial cleanup)

- [ ] Verify `src/control/cli.py` provides a command-line integration entrypoint.
- [ ] Verify the `correct`, `missing_gasket`, `wrong_cover`, and `wrong_order` scenarios behave correctly.
- [ ] Verify mock mode is clearly distinguished from real simulation/AI mode.
- [ ] Verify corrections allow reinspection and completion without silently skipping checks.
- [ ] Verify complete integration tests run without requiring Ollama or a GPU.
- [ ] Verify all existing project tests still pass.

**Important:** The presence of these files and tests must be confirmed from the **local `feat/control` working tree**. Code not yet pushed is not visible to collaborators through GitHub.

## 5. Acceptance criteria before opening a merge-ready PR

- [ ] `python -m pytest` runs successfully; record the real test count below.
- [ ] All four assembly steps can reach `COMPLETE` in the supported demo mode.
- [ ] Missing gasket prevents progress and can be corrected/reinspected.
- [ ] Wrong cover is rejected and can be replaced/reinspected.
- [ ] Out-of-order action is blocked **before** invoking the inspector.
- [ ] Low-confidence results never pass automatically.
- [ ] Timeouts/errors never pass automatically; Hold can be cleared safely without advancing.
- [ ] Manual overrides require typed identity/reason and are permanently distinguishable from rule-based passes.
- [ ] No step advances except after authorized PASS or a properly logged override.
- [ ] Audit records remain valid, append-only JSONL; outputs are not committed.
- [ ] No shared contracts were modified without team coordination.
- [ ] No unnecessary modifications to other partners' files.
- [ ] Review the actual Git diff for generated or unrelated files.

### Verification record (fill in after running locally)

```text
Date/time:
Branch:
Commit SHA:
Command: python -m pytest
Result: ___ passed, ___ failed, ___ skipped
CLI smoke test command:
CLI smoke test result:
Real simulator integrated? yes / no / mock only
Real inspector integrated? yes / no / mock only
Open issues:
```

## 6. Remaining work and next steps

The original roadmap assigns the dashboard to Partner 4. The following is the **control team's** follow-up work rather than a request to build the dashboard.

### Immediate next session

1. Review Codex's changes and confirm all tests; fix failures and incomplete contracts.
2. Check integration points with Partners 1 and 2: `AssemblyEnv`, inspector object, `MockInspector`, timeout/failure behavior, and scenario definitions.
3. Decide and document the precise treatment of fallback observations vs. actual timeouts. A zero-confidence result alone does not necessarily prove a timeout.
4. Run CLI scenarios in mock mode. Run real mode only after the simulator and inspector interfaces exist and are ready.
5. Share `Workflow.status()` and `operator_override()` calling conventions with Partner 4 for the Streamlit integration.
6. Request teammate review before merging to `main`.

### Roadmap milestones

| Target | Control work / team dependency |
|---|---|
| **Oct 29** | Audit logger and rule tests verified |
| **Oct 30–31** | Correction messages, low-confidence handling, order guard verified |
| **Nov 1** | CLI end-to-end milestone with supported dependencies |
| **Nov 2–3** | Operator override; Hold and recovery; dashboard integration assistance |
| **Nov 4–5** | Safety invariant tests and mock-based integration tests; bug bash |
| **Nov 6–7** | Cleanup, control-logic writeup section, optional audit CSV export |
| **Nov 8** | Feature freeze; review and bug fixes only afterwards |
| **Nov 10–11** | Offline rehearsal, final review, and submission |

### Later enhancements (lower priority)

- `CTL-12`: Optional behavior-tree diagram for the writeup.
- `CTL-13`: Optional audit JSONL-to-CSV export.
- Continue reviewing teammate PRs that touch control interfaces.

## 7. Files expected to be owned by Partner 3

```text
src/control/
    __init__.py
    workflow.py
    rules.py
    audit_log.py
    cli.py

tests/
    test_control_*.py      # actual file names may differ

src/vision/schemas.py      # shared schema: change only with team approval
```

The full project also has `config/assembly.yaml` and `src/config.py`; consume these as existing interfaces rather than duplicating configuration in control code.

## 8. Git workflow for this team

Team workflow: **individual feature branches → pull requests into `main`**. There is **no `dev` branch**.

```powershell
# On your own branch:
git status
git branch --show-current        # should show feat/control
python -m pytest

# Review before staging:
git diff --stat
git status --short

# Stage the intended work, including this document once copied into the repo:
git add src/control tests CONTROL_MODULE_PROGRESS.md

# Inspect staged changes:
git diff --cached --stat

git commit -m "feat(control): implement workflow rules and safeguards"
git push -u origin feat/control
```

Open a pull request on GitHub with **base: `main`** and **compare: `feat/control`**. Do not merge until tests and team integration checks are satisfactory. If the work is still in progress, open a **Draft PR** and mark it ready later. Avoid including `.venv/`, `outputs/`, generated images, model files, or secrets.

## 9. Known integration questions to resolve

- Does the actual rules API accept enough context to determine all cumulative expected parts?
- What exact result does `check_action()` return, and how do simulator/dashboard callers consume it?
- How does the inspector distinguish a model timeout from an ordinary low-confidence observation within current shared contracts?
- When a verified component is removed or changed, which approvals are invalidated and how is that event provided to control?
- What happens if audit persistence fails immediately before a proposed step advancement?
- Does the CLI run with **real** simulator/inspector implementations, or only explicitly named mocks so far?
- Are any current test failures due to teammates' incomplete modules versus control defects?

## 10. Handoff note

The control system is the authorization boundary for HoloGuide. Its central promise is not that visual AI is infallible; it is that the AI's uncertain or incorrect observation cannot independently advance the assembly. The final demonstration should show a correct run, missing/wrong/out-of-order faults, correction and reinspection, safe handling of uncertainty/failure, and a reasoned, auditable manual override.

**Next update:** Replace unchecked items with verified outcomes after running the local tests and reviewing the working tree. Keep this file current in `feat/control` before merging.
