"""Runnable end-to-end command-line entry point for HoloGuide control.

Mock mode writes labelled JSON fixtures and makes no AI or simulation claims.
Real mode delegates rendered scenario playback to ``simulation.runner`` using
PyBullet and an installed vision factory (OpenCV by default). Both modes keep
authorization in the production :class:`Workflow`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

if __package__ and __package__.startswith("src."):
    from ..config import AssemblyConfig, DEFAULT_CONFIG_PATH, StepSpec, load_config
    from ..vision.schemas import Decision, DecisionStatus, Observation
    from .audit_log import AuditLog, AuditLogError
    from .workflow import Inspector, Workflow, WorkflowState
else:
    from config import AssemblyConfig, DEFAULT_CONFIG_PATH, StepSpec, load_config
    from control.audit_log import AuditLog, AuditLogError
    from control.workflow import Inspector, Workflow, WorkflowState
    from vision.schemas import Decision, DecisionStatus, Observation


SCENARIOS = ("correct", "missing_gasket", "wrong_cover", "wrong_order")
DEFAULT_REAL_INSPECTOR_FACTORY = "src.vision.factories:cv"


class ScenarioConfigurationError(RuntimeError):
    """Raised when a named demo scenario cannot be mapped to the loaded config."""


class MockAssemblyEnvironment:
    """Small deterministic scene test double; it is not a physical simulator."""

    def __init__(self) -> None:
        self.visible_parts: set[str] = set()
        self.applied_actions: list[tuple[str, str]] = []
        self.render_count = 0

    def place_part(self, requested_part: str, *, visible_as: str | None = None) -> None:
        visible_part = visible_as or requested_part
        self.visible_parts.add(visible_part)
        self.applied_actions.append((requested_part, visible_part))

    def remove_part(self, visible_part: str) -> None:
        self.visible_parts.discard(visible_part)

    def render(self, directory: Path, scenario: str, step_id: int) -> Path:
        """Write a labelled mock scene snapshot, not a camera image."""
        directory.mkdir(parents=True, exist_ok=True)
        self.render_count += 1
        path = directory / (
            f"{scenario}_step_{step_id}_mock_frame_{self.render_count}.json"
        )
        payload = {
            "mode": "mock",
            "format": "synthetic-visible-parts-snapshot",
            "visible_parts": sorted(self.visible_parts),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


class MockInspector:
    """Deterministic fixture inspector; this is explicitly not AI inference.

    Its confidence value represents certainty of the controlled fixture mapping,
    not measured model accuracy.
    """

    def __init__(self, config: AssemblyConfig) -> None:
        self.config = config
        self.inspection_count = 0
        self.inspected_frames: list[Path] = []

    def inspect(self, image_path: str | Path, step: StepSpec) -> Observation:
        path = Path(image_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("mode") != "mock":
            raise ValueError("mock inspector received a non-mock frame")
        visible = set(payload.get("visible_parts", []))
        expected = self.config.expected_parts(step.id)
        decoys = {part.name for part in self.config.parts if part.is_decoy}
        known = {part.name for part in self.config.parts}
        wrong = (visible & decoys) | (visible - known)
        self.inspection_count += 1
        self.inspected_frames.append(path)
        return Observation(
            parts_detected=sorted(visible),
            missing_parts=sorted(expected - visible),
            wrong_parts=sorted(wrong),
            confidence=1.0,
            rationale="Deterministic mock fixture observation; not AI inference.",
        )


@dataclass
class ScenarioResult:
    scenario: str
    workflow: Workflow
    environment: MockAssemblyEnvironment
    inspector: Inspector
    decisions: list[Decision] = field(default_factory=list)
    corrections: int = 0
    safe_stop_reason: str | None = None

    @property
    def completed(self) -> bool:
        return self.workflow.state is WorkflowState.COMPLETE


@dataclass
class RealScenarioExecution:
    """Result of the thin control-CLI adapter around ``ScenarioRunner``."""

    scenario: str
    workflow: Workflow
    inspector: Inspector
    run_result: Any
    frames_dir: Path

    @property
    def completed(self) -> bool:
        return (
            bool(self.run_result.completed)
            and self.run_result.error is None
            and self.workflow.state is WorkflowState.COMPLETE
        )


def run_mock_scenario(
    scenario: str,
    *,
    config: AssemblyConfig | None = None,
    environment: MockAssemblyEnvironment | None = None,
    inspector: Inspector | None = None,
    audit: AuditLog | None = None,
    frames_dir: Path | str = Path("outputs/mock_frames"),
    output: TextIO | None = None,
    retry_limit: int = 3,
) -> ScenarioResult:
    """Run one deterministic scenario through the real control workflow."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario '{scenario}'")
    output = output if output is not None else sys.stdout
    config = config if config is not None else load_config()
    environment = (
        environment if environment is not None else MockAssemblyEnvironment()
    )
    inspector = inspector if inspector is not None else MockInspector(config)
    audit = audit if audit is not None else AuditLog()
    frames_dir = Path(frames_dir)

    print("[MODE] MOCK - deterministic fixtures; no Ollama, GPU, or PyBullet.", file=output)
    print(f"[SCENARIO] {scenario}", file=output)
    audit.record("scenario_started", scenario=scenario, mode="mock")
    workflow = Workflow(config, inspector, audit, retry_limit=retry_limit)
    result = ScenarioResult(scenario, workflow, environment, inspector)

    missing_target = (
        _part_for_named_scenario(config, "gasket")
        if scenario == "missing_gasket"
        else None
    )
    wrong_parts = _wrong_cover_parts(config) if scenario == "wrong_cover" else None
    wrong_visible = wrong_parts[0] if wrong_parts else None
    wrong_target = wrong_parts[1] if wrong_parts else None
    missing_fault_done = scenario != "missing_gasket"
    wrong_cover_fault_done = scenario != "wrong_cover"

    if scenario == "wrong_order":
        if len(config.steps) < 2 or not config.steps[-1].adds_parts:
            raise ScenarioConfigurationError("wrong_order requires at least two populated steps")
        premature_part = config.steps[-1].adds_parts[0]
        before_inspections = _inspection_count(inspector)
        guard = workflow.check_action(premature_part)
        print(f"[GUARD] BLOCKED: {guard.reason}", file=output)
        if guard.allowed or _inspection_count(inspector) != before_inspections:
            return _safe_stop(
                result,
                audit,
                output,
                "Wrong-order action was not safely rejected before inspection.",
            )
        result.corrections += 1
        print("[CORRECTION] Returning to the configured current-step part.", file=output)

    while workflow.state is not WorkflowState.COMPLETE:
        step = workflow.current_step

        if (
            not missing_fault_done
            and missing_target is not None
            and missing_target in step.adds_parts
        ):
            missing_fault_done = True
            print(
                f"[MOCK FAULT] {missing_target} is visibly absent before inspection.",
                file=output,
            )
            decision = _inspect(
                result, audit, frames_dir, output
            )
            if decision.status is not DecisionStatus.BLOCK:
                return _safe_stop(
                    result,
                    audit,
                    output,
                    "Missing-part fixture did not produce BLOCK.",
                )
            result.corrections += 1
            print(f"[CORRECTION] Adding {missing_target} and reinspecting.", file=output)

        visible_overrides: dict[str, str] = {}
        if (
            not wrong_cover_fault_done
            and wrong_target is not None
            and wrong_target in step.adds_parts
        ):
            wrong_cover_fault_done = True
            visible_overrides[wrong_target] = wrong_visible

        for part in step.adds_parts:
            guard = workflow.check_action(part)
            label = "ALLOWED" if guard.allowed else "BLOCKED"
            print(f"[GUARD] {label}: {guard.reason}", file=output)
            if not guard.allowed:
                return _safe_stop(
                    result,
                    audit,
                    output,
                    f"Current-step action was rejected: {guard.reason}",
                )
            visible_as = visible_overrides.get(part)
            environment.place_part(part, visible_as=visible_as)
            if visible_as:
                print(
                    f"[MOCK FAULT] Requested {part}, but the visible fixture is "
                    f"{visible_as}.",
                    file=output,
                )

        decision = _inspect(result, audit, frames_dir, output)
        if decision.status is DecisionStatus.PASS:
            continue
        if (
            scenario == "wrong_cover"
            and decision.status is DecisionStatus.BLOCK
            and wrong_visible is not None
            and wrong_target is not None
            and wrong_visible in environment.visible_parts
        ):
            result.corrections += 1
            print(
                f"[CORRECTION] Removing {wrong_visible}, adding {wrong_target}, "
                "and reinspecting.",
                file=output,
            )
            environment.remove_part(wrong_visible)
            guard = workflow.check_action(wrong_target)
            print(
                f"[GUARD] {'ALLOWED' if guard.allowed else 'BLOCKED'}: {guard.reason}",
                file=output,
            )
            if not guard.allowed:
                return _safe_stop(result, audit, output, guard.reason)
            environment.place_part(wrong_target)
            corrected = _inspect(result, audit, frames_dir, output)
            if corrected.status is DecisionStatus.PASS:
                continue
            return _safe_stop(
                result,
                audit,
                output,
                f"Wrong-cover correction did not pass: {corrected.message}",
            )
        return _safe_stop(
            result,
            audit,
            output,
            f"Workflow stopped safely on {decision.status.value}: {decision.message}",
        )

    audit.record(
        "scenario_completed",
        scenario=scenario,
        mode="mock",
        final_state=workflow.state,
        corrections=result.corrections,
    )
    _print_summary(result, output)
    return result


def run_real_scenario(
    scenario: str,
    *,
    config: AssemblyConfig | None = None,
    inspector: Inspector | None = None,
    inspector_factory: str = DEFAULT_REAL_INSPECTOR_FACTORY,
    step_id_interface: bool = False,
    audit: AuditLog | None = None,
    frames_dir: Path | str | None = None,
    output: TextIO | None = None,
    retry_limit: int = 3,
    seed: int = 0,
) -> RealScenarioExecution:
    """Run a rendered scenario through the existing simulator integration.

    Imports are deliberately lazy so mock mode remains usable on machines that
    do not have PyBullet or OpenCV installed. ``ScenarioRunner`` owns scenario
    orchestration; this adapter only supplies production dependencies and output.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario '{scenario}'")
    output = output if output is not None else sys.stdout
    config = config if config is not None else load_config()
    audit = audit if audit is not None else AuditLog()
    frames_path = (
        Path(frames_dir)
        if frames_dir is not None
        else _default_real_frames_dir(scenario)
    )

    # Importing runner also imports the real PyBullet environment. Keeping this
    # here prevents an optional real dependency from breaking explicit mock use.
    from src.simulation.assembly_env import AssemblyEnv
    from src.simulation.runner import ScenarioRunner

    if inspector is None:
        inspector = _load_real_inspector(
            inspector_factory,
            config,
            step_id_interface=step_id_interface,
        )
    if not callable(getattr(inspector, "inspect", None)):
        raise TypeError("inspector factory did not return an object with inspect()")

    print(
        "[MODE] REAL - PyBullet rendered PNGs with inspector factory "
        f"{inspector_factory}.",
        file=output,
    )
    print(f"[SCENARIO] {scenario}", file=output)
    print(f"[FRAMES] {frames_path}", file=output)

    workflow = Workflow(config, inspector, audit, retry_limit=retry_limit)
    with AssemblyEnv(config, seed=seed) as environment:
        runner = ScenarioRunner(
            scenario,
            environment,
            workflow,
            frames_path,
        )
        run_result = runner.run()

    execution = RealScenarioExecution(
        scenario=scenario,
        workflow=workflow,
        inspector=inspector,
        run_result=run_result,
        frames_dir=frames_path,
    )
    if not execution.completed:
        audit.record(
            "scenario_stopped",
            scenario=scenario,
            mode="real",
            final_state=workflow.state,
            current_step=workflow.current_step,
            reason=run_result.error or "scenario did not reach COMPLETE",
        )
    _print_real_summary(execution, output)
    return execution


def _inspect(
    result: ScenarioResult,
    audit: AuditLog,
    frames_dir: Path,
    output: TextIO,
) -> Decision:
    frame = result.environment.render(
        frames_dir,
        result.scenario,
        result.workflow.current_step_id,
    )
    print(f"[MOCK FRAME] {frame} (JSON snapshot, not an image)", file=output)
    decision = result.workflow.request_inspection(frame)
    result.decisions.append(decision)
    print(
        f"[DECISION] {decision.status.value.upper()}: {decision.message}",
        file=output,
    )
    print(
        f"[STATE] step={result.workflow.current_step_id} "
        f"state={result.workflow.state.value}",
        file=output,
    )
    return decision


def _safe_stop(
    result: ScenarioResult,
    audit: AuditLog,
    output: TextIO,
    reason: str,
) -> ScenarioResult:
    result.safe_stop_reason = reason
    audit.record(
        "scenario_stopped",
        scenario=result.scenario,
        mode="mock",
        final_state=result.workflow.state,
        current_step=result.workflow.current_step,
        reason=reason,
    )
    print(f"[SAFE STOP] {reason}", file=output)
    _print_summary(result, output)
    return result


def _print_summary(result: ScenarioResult, output: TextIO) -> None:
    outcome = "COMPLETE" if result.completed else "SAFE STOP"
    print("\n=== HoloGuide scenario summary ===", file=output)
    print(f"Scenario: {result.scenario}", file=output)
    print("Mode: MOCK (deterministic fixtures, not AI)", file=output)
    print(f"Outcome: {outcome}", file=output)
    print(f"Workflow state: {result.workflow.state.value}", file=output)
    print(f"Current step: {result.workflow.current_step_id}", file=output)
    print(f"Inspections: {_inspection_count(result.inspector)}", file=output)
    print(f"Corrections: {result.corrections}", file=output)


def _inspection_count(inspector: Inspector) -> int:
    value = getattr(inspector, "inspection_count", None)
    if isinstance(value, int):
        return value
    value = getattr(inspector, "calls", None)
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return len(value)
    return 0


def _part_for_named_scenario(config: AssemblyConfig, name: str) -> str:
    if not any(part.name == name for part in config.parts):
        raise ScenarioConfigurationError(
            f"scenario requires configured part '{name}'"
        )
    if not any(name in step.adds_parts for step in config.steps):
        raise ScenarioConfigurationError(
            f"scenario part '{name}' is not assigned to a step"
        )
    return name


def _wrong_cover_parts(config: AssemblyConfig) -> tuple[str, str]:
    decoy = next((part for part in config.parts if part.is_decoy), None)
    if decoy is None:
        raise ScenarioConfigurationError("wrong_cover requires a configured decoy part")
    candidate = decoy.name.removeprefix("wrong_")
    if not any(candidate in step.adds_parts for step in config.steps):
        raise ScenarioConfigurationError(
            f"decoy '{decoy.name}' has no matching assembly part"
        )
    return decoy.name, candidate


def _default_real_frames_dir(scenario: str) -> Path:
    """Choose a collision-resistant directory because ScenarioRunner is exclusive."""
    return Path("outputs/real_runs") / f"{scenario}-{uuid4().hex[:12]}" / "frames"


def _load_real_inspector(
    spec: str,
    config: AssemblyConfig,
    *,
    step_id_interface: bool,
) -> Inspector:
    """Load a genuine vision inspector while honoring a custom assembly config."""
    if spec == DEFAULT_REAL_INSPECTOR_FACTORY and not step_id_interface:
        from src.vision.factories import cv

        return cv(config)

    from src.simulation.runner import load_inspector

    return load_inspector(spec, step_id_interface)


def _print_real_summary(result: RealScenarioExecution, output: TextIO) -> None:
    outcome = "COMPLETE" if result.completed else "SAFE STOP"
    print("\n=== HoloGuide scenario summary ===", file=output)
    print(f"Scenario: {result.scenario}", file=output)
    print("Mode: REAL (PyBullet rendering and configured inspector)", file=output)
    print(f"Outcome: {outcome}", file=output)
    print(f"Workflow state: {result.workflow.state.value}", file=output)
    print(f"Current step: {result.workflow.current_step_id}", file=output)
    print(f"Decisions: {', '.join(result.run_result.decisions) or 'none'}", file=output)
    print(f"Guard blocked premature action: {result.run_result.guard_blocked}", file=output)
    print(f"Recovered from injected fault: {result.run_result.recovered}", file=output)
    print(f"Frames: {result.frames_dir}", file=output)
    if result.run_result.error:
        print(f"Failure: {result.run_result.error}", file=output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the HoloGuide deterministic assembly control workflow."
    )
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    parser.add_argument(
        "--mode",
        choices=("mock", "real"),
        default="mock",
        help=(
            "mock uses deterministic JSON fixtures (default); real uses PyBullet "
            "rendering and a configured vision inspector"
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--audit-path",
        type=Path,
        default=Path("outputs/audit.jsonl"),
    )
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=None,
        help=(
            "frame directory; mock defaults to outputs/mock_frames, real defaults "
            "to a unique directory under outputs/real_runs"
        ),
    )
    parser.add_argument("--retry-limit", type=int, default=3)
    parser.add_argument(
        "--inspector-factory",
        default=DEFAULT_REAL_INSPECTOR_FACTORY,
        help=(
            "real-mode module:function factory (default: "
            f"{DEFAULT_REAL_INSPECTOR_FACTORY})"
        ),
    )
    parser.add_argument(
        "--step-id-interface",
        action="store_true",
        help="adapt a custom real inspector that expects an integer step id",
    )
    parser.add_argument("--seed", type=int, default=0, help="real simulator seed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        audit = AuditLog(args.audit_path)
        if args.mode == "real":
            result = run_real_scenario(
                args.scenario,
                config=config,
                inspector_factory=args.inspector_factory,
                step_id_interface=args.step_id_interface,
                audit=audit,
                frames_dir=args.frames_dir,
                retry_limit=args.retry_limit,
                seed=args.seed,
            )
            return 0 if result.completed else 1

        result = run_mock_scenario(
            args.scenario,
            config=config,
            audit=audit,
            frames_dir=args.frames_dir or Path("outputs/mock_frames"),
            retry_limit=args.retry_limit,
        )
        return 0 if result.completed else 1
    except (
        AuditLogError,
        ImportError,
        OSError,
        TypeError,
        ValueError,
        ScenarioConfigurationError,
    ) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
