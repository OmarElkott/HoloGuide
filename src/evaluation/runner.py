"""Run an existing Inspector over an explicit CSV manifest, without label leakage.

No dataset ships with this checkout. The provisional manifest adapter is documented
in README; it does not assert a shared dataset contract.
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

from src.config import load_config
from src.control.cli import MockInspector
from src.control.rules import RuleContext, evaluate
from src.vision.schemas import DecisionStatus, Observation
from src.evaluation.metrics import (
    accuracy_by_mistake_type, false_block_rate, latency_statistics,
)

FIELDS = ['image_path', 'step_id', 'mistake_type', 'expected_status', 'status',
          'confidence', 'latency_s', 'message', 'rationale', 'error', 'inspector']


def run(manifest, inspector, config, output):
    """Inspect independent cases; only path and StepSpec reach the inspector.

    Validate the complete manifest before invoking inspection or writing results.
    A failed call is retained as HOLD and included in accuracy and latency metrics.
    Output files must be new, preventing accidental replacement of previous runs.
    """
    manifest, output = Path(manifest), Path(output)
    summary_path = output.with_suffix('.summary.json')
    table_path = output.with_suffix('.tables.md')
    if output.exists() or summary_path.exists() or table_path.exists():
        raise FileExistsError('choose a new output path for this evaluation run')
    with manifest.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'image_path', 'step_id', 'mistake_type', 'expected_status'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f'manifest requires columns: {sorted(required)}')
        cases = list(reader)
    if not cases:
        raise ValueError('manifest contains no evaluation cases')
    prepared = []
    for case in cases:
        step_id = int(case['step_id'])
        if not 1 <= step_id <= len(config.steps):
            raise ValueError(f'invalid step_id: {step_id}')
        DecisionStatus(case['expected_status'])
        if not case['mistake_type'].strip():
            raise ValueError('mistake_type must be non-empty')
        image = (manifest.parent / case['image_path']).resolve()
        if not image.is_file():
            raise FileNotFoundError(image)
        prepared.append((case, image, config.step(step_id)))
    rows = []
    for case, image, step in prepared:
        start = perf_counter()
        error = ''
        observation = None
        try:
            observation = Observation.model_validate(inspector.inspect(image, step))
            decision = evaluate(observation, step, config.confidence_threshold,
                                context=RuleContext.from_config(config, step))
            status, message = decision.status.value, decision.message
        except Exception as exc:
            status, message = 'hold', 'Inspection failed; no approval.'
            error = f'{type(exc).__name__}: {exc}'
        elapsed = perf_counter() - start
        rows.append({
            'image_path': case['image_path'], 'step_id': step.id,
            'mistake_type': case['mistake_type'],
            'expected_status': case['expected_status'], 'status': status,
            'confidence': observation.confidence if observation else None,
            'latency_s': elapsed, 'message': message,
            'rationale': observation.rationale if observation else '',
            'error': error, 'inspector': type(inspector).__name__,
        })
    summary = {
        'inspector': type(inspector).__name__,
        'mock': isinstance(inspector, MockInspector),
        'accuracy_by_mistake_type': accuracy_by_mistake_type(rows),
        'false_block_rate': false_block_rate(rows),
        'latency': latency_statistics(row['latency_s'] for row in rows),
        'status_counts': dict(Counter(row['status'] for row in rows)),
        'error_count': sum(bool(row['error']) for row in rows),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with summary_path.open('x', encoding='utf-8') as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write('\n')
    with table_path.open('x', encoding='utf-8') as stream:
        stream.write(f"Inspector: {summary['inspector']}; mock: {summary['mock']}\n\n")
        stream.write('| Mistake type | Cases | Decision accuracy |\n|---|---:|---:|\n')
        for kind, metric in summary['accuracy_by_mistake_type'].items():
            label = kind.replace('|', '/').replace('\n', ' ')
            stream.write(f"| {label} | {metric['count']} | {metric['accuracy']:.4f} |\n")
        stream.write(f"\nFalse-block rate: {summary['false_block_rate']}\n\n")
        stream.write('| Latency statistic | Seconds |\n|---|---:|\n')
        for key in ('mean_s', 'median_s', 'p95_s'):
            stream.write(f"| {key} | {summary['latency'][key]} |\n")
        stream.write(f"\nErrors: {summary['error_count']}; status counts: {summary['status_counts']}\n")
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = load_config()
    _, summary = run(args.manifest, MockInspector(config), config, args.output)
    print('MOCK evaluation only; not measured AI performance.')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
