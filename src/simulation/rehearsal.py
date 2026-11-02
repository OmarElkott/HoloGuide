"""Offline rendered rehearsal with conspicuously scripted observations, not AI.

Run: python -m src.simulation.rehearsal --output outputs/rehearsal-001
"""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw
from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.runner import ScenarioRunner
from src.simulation.scenarios import NAMES
from src.vision.schemas import Observation


class RehearsalInspector:
    """Prewritten observations keyed to script position; never image analysis.

    This test double does not read simulator state, label CSVs, or image contents.
    It exists only to rehearse control, rendering and UI while P2 is unavailable.
    """
    def __init__(self, scenario):
        if scenario not in NAMES:
            raise ValueError(f'Unknown scenario: {scenario}')
        self.scenario = scenario
        self.injected = False
        self.config = load_config()

    def inspect(self, image_path, step):
        detected = sorted(self.config.expected_parts(step.id))
        trigger = {'missing_gasket': 2, 'wrong_cover': 3, 'wrong_order': 1}.get(self.scenario)
        if not self.injected and step.id == trigger:
            self.injected = True
            if self.scenario == 'missing_gasket':
                detected.remove('gasket')
                detected.append('cover')
            elif self.scenario == 'wrong_cover':
                detected.remove('cover')
                detected.append('wrong_cover')
            else:
                detected.append('cover')
        return Observation(parts_detected=detected, confidence=1,
                           rationale='SCRIPTED REHEARSAL: no image analysis or AI inference')


def rehearse(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = load_config()
    results, frames = [], []
    for scenario in NAMES:
        directory = output / scenario
        with AssemblyEnv(config, seed=2026) as env:
            workflow = Workflow(config, RehearsalInspector(scenario), AuditLog(directory / 'audit.jsonl'))
            runner = ScenarioRunner(scenario, env, workflow, directory / 'frames')
            while not runner.done:
                runner.tick()
                if runner.frame:
                    with Image.open(runner.frame) as source:
                        frame = Image.new('RGB', (640, 560), '#142b41')
                        frame.paste(source.convert('RGB').resize((640, 480)), (0, 40))
                    draw = ImageDraw.Draw(frame)
                    draw.text((12, 10), 'SCRIPTED REHEARSAL - NOT AI PERFORMANCE', fill='#ffd06a')
                    draw.text((12, 530), f'{scenario} | tick {runner.ticks} | {runner.result.decisions[-1]}', fill='white')
                    frames.append(frame)
            results.append(asdict(runner.result))
    (output / 'rehearsal.json').write_text(json.dumps({
        'evidence': 'test-double', 'results': results,
        'warning': 'Prewritten observations; no image analysis. Not a final competition video.'
    }, indent=2), encoding='utf-8')
    frames[0].save(output / 'rehearsal.gif', save_all=True, append_images=frames[1:], duration=1500, loop=0)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = rehearse(args.output)
    print('SCRIPTED REHEARSAL ONLY: no AI performance measured.')
    print(json.dumps(results, indent=2))
    if not all(r['completed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
