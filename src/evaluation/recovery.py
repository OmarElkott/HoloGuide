"""Recovery trials using rendered images and the production workflow."""
import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path
from src.config import load_config
from src.control.audit_log import AuditLog
from src.control.workflow import Workflow
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.runner import ScenarioRunner, load_inspector
from src.simulation.scenarios import NAMES


def run_trials(output, inspector_factory, *, runs=20, seed=0, evidence='unverified'):
    if runs < 1:
        raise ValueError('runs must be positive')
    if evidence not in {'test-double', 'real-inspector', 'unverified'}:
        raise ValueError('Invalid evidence classification')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = load_config()
    rows = []
    for scenario in NAMES:
        for trial in range(runs):
            directory = output / scenario / str(trial)
            inspector = inspector_factory()
            with AssemblyEnv(config, seed=seed + trial, randomize=True) as env:
                workflow = Workflow(config, inspector, AuditLog(directory / 'audit.jsonl'))
                result = ScenarioRunner(scenario, env, workflow, directory / 'frames').run()
            row = asdict(result)
            row.update(trial=trial, seed=seed + trial, evidence=evidence,
                       inspector=type(inspector).__name__)
            row['decisions'] = json.dumps(row['decisions'])
            rows.append(row)
    with (output / 'recovery.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'provenance.json').write_text(json.dumps({
        'evidence': evidence, 'runs_per_scenario': runs, 'seed': seed,
        'config': config.model_dump(mode='json'),
        'interpretation': 'Test doubles establish plumbing only, never AI performance. Recovery requires block then correction pass; completion is reported separately.'
    }, indent=2), encoding='utf-8')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inspector-factory', required=True)
    parser.add_argument('--step-id-interface', action='store_true')
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--evidence', choices=['test-double', 'real-inspector', 'unverified'], default='unverified')
    args = parser.parse_args()
    rows = run_trials(args.output, lambda: load_inspector(args.inspector_factory, args.step_id_interface),
                      runs=args.runs, seed=args.seed, evidence=args.evidence)
    print(f'{len(rows)} trials saved; evidence={args.evidence}')


if __name__ == '__main__':
    main()
