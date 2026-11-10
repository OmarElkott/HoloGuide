"""Run an existing Inspector over an explicit CSV manifest, without label leakage.

The manifest contract is supplied by src.simulation.dataset; labels never reach
the inspector. Returned P2 failure/timeout markers are retained as evidence.
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
from src.evaluation.provenance import collect, sha256
from src.evaluation.diagnostics import counters, outcome

FIELDS = ['image_path', 'step_id', 'mistake_type', 'expected_status', 'status',
          'confidence', 'latency_s', 'message', 'rationale', 'error', 'inspector', 'timed_out', 'inspector_status']


def run(manifest, inspector, config, output, *, model=None, endpoint=None):
    """Inspect independent cases; only path and StepSpec reach the inspector.

    Validate the complete manifest before invoking inspection or writing results.
    A failed call is retained as HOLD and included in accuracy and latency metrics.
    Output files must be new, preventing accidental replacement of previous runs.
    """
    manifest, output = Path(manifest), Path(output)
    summary_path = output.with_suffix('.summary.json')
    table_path = output.with_suffix('.tables.md')
    provenance_path = output.with_suffix('.provenance.json')
    if any(path.exists() for path in (output, summary_path, table_path, provenance_path)):
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
        if case.get('sha256') and case['sha256'] != sha256(image):
            raise ValueError(f'Dataset image checksum mismatch: {image.name}')
        prepared.append((case, image, config.step(step_id)))
    metadata_path = manifest.parent / 'dataset.json'
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if metadata.get('config') != config.model_dump(mode='json'):
            raise ValueError('Dataset configuration does not match evaluation configuration')
    rows = []
    provenance = collect(manifest, config, inspector, prepared, model=model, endpoint=endpoint)
    for case, image, step in prepared:
        start = perf_counter()
        error = ''
        observation = None
        timed_out = False
        inspector_status = ''
        before = counters(inspector)
        try:
            observation = Observation.model_validate(inspector.inspect(image, step))
            timed_out, error, inspector_status = outcome(observation, before)
            decision = evaluate(observation, step, config.confidence_threshold,
                                context=RuleContext.from_config(config, step))
            status, message = decision.status.value, decision.message
        except Exception as exc:
            status, message = 'hold', 'Inspection failed; no approval.'
            error = f'{type(exc).__name__}: {exc}'
            timed_out = isinstance(exc, TimeoutError)
        elapsed = perf_counter() - start
        rows.append({
            'image_path': case['image_path'], 'step_id': step.id,
            'mistake_type': case['mistake_type'],
            'expected_status': case['expected_status'], 'status': status,
            'confidence': observation.confidence if observation else None,
            'latency_s': elapsed, 'message': message,
            'rationale': observation.rationale if observation else '',
            'error': error, 'inspector': type(inspector).__name__,
            'timed_out': timed_out,
            'inspector_status': inspector_status,
        })
    summary = {
        'inspector': type(inspector).__name__,
        'mock': isinstance(inspector, MockInspector),
        'accuracy_by_mistake_type': accuracy_by_mistake_type(rows),
        'false_block_rate': false_block_rate(rows),
        'latency': latency_statistics(row['latency_s'] for row in rows),
        'status_counts': dict(Counter(row['status'] for row in rows)),
        'error_count': sum(bool(row['error']) for row in rows),
        'timeout_count': sum(row['timed_out'] for row in rows),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with provenance_path.open('x', encoding='utf-8') as stream:
        json.dump(provenance, stream, indent=2, allow_nan=False)
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
    parser.add_argument('--inspector-factory', help='module:function returning configured real inspector')
    parser.add_argument('--step-id-interface', action='store_true', help='adapt P2 inspect(path, step_id) to P3 StepSpec interface')
    parser.add_argument('--model')
    parser.add_argument('--endpoint')
    args = parser.parse_args()
    config = load_config()
    if args.inspector_factory:
        import importlib
        module, name = args.inspector_factory.split(':', 1)
        inspector = getattr(importlib.import_module(module), name)()
        if args.step_id_interface:
            from src.dashboard.backends import StepIdInspectorAdapter
            inspector = StepIdInspectorAdapter(inspector)
    else:
        if args.step_id_interface:
            parser.error('--step-id-interface requires --inspector-factory')
        inspector = MockInspector(config)
    _, summary = run(args.manifest, inspector, config, args.output,
                     model=args.model, endpoint=args.endpoint)
    print('MOCK evaluation only; not measured AI performance.' if summary['mock'] else 'Configured inspector evaluation; verify provenance before interpreting results.')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
