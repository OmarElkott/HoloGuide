"""Regenerate writeup tables from saved CSVs without invoking any inspector."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from src.evaluation.metrics import accuracy_by_mistake_type, false_block_rate, latency_statistics

MODELS = ('Gemma only', 'OpenCV only', 'Hybrid')


def read_results(path):
    with Path(path).open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'mistake_type', 'expected_status', 'status', 'latency_s'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f'Results require {sorted(required)}')
        rows = list(reader)
    if not rows:
        raise ValueError('Results CSV contains no cases')
    for row in rows:
        if row['status'] not in {'pass', 'block', 'hold', 'needs_review'} or row['expected_status'] not in {'pass', 'block', 'hold', 'needs_review'}:
            raise ValueError('Invalid decision status in results')
        if not row['mistake_type'].strip():
            raise ValueError('Empty mistake type in results')
        row['latency_s'] = float(row['latency_s'])
    latency_statistics(row['latency_s'] for row in rows)
    return rows


def summarize(rows):
    return {
        'accuracy_by_mistake_type': accuracy_by_mistake_type(rows),
        'false_block_rate': false_block_rate(rows),
        'latency': latency_statistics(row['latency_s'] for row in rows),
        'status_counts': dict(Counter(row['status'] for row in rows)),
        'error_count': sum(bool(row.get('error')) for row in rows),
        # Only explicit timeout evidence is counted. HOLD is not proof of timeout.
        'timeout_count': sum(row.get('timed_out') == 'True' or
                             row.get('error', '').startswith('TimeoutError:') for row in rows),
    }


def generate_report(results, output, *, recovery=None, safety=None):
    """results maps explicit display names to CSV paths, avoiding model guessing.

    Recovery/safety JSON are displayed as supplied evidence, not synthesized into
    unagreed P1/P3 schemas. They remain optional until team contracts are available.
    """
    datasets = {name: read_results(path) for name, path in results.items()}
    summaries = {name: summarize(rows) for name, rows in datasets.items()}
    names = list(dict.fromkeys((*MODELS, *results)))
    kinds = sorted({row['mistake_type'] for rows in datasets.values() for row in rows})
    def clean(value):
        return str(value).replace('|', '/').replace('\n', ' ')
    lines = ['# Evaluation tables', '',
             'N/A means no measurement supplied. Mock data is not model performance.', '',
             '| Mistake type | ' + ' | '.join(clean(n) for n in names) + ' |',
             '|---|' + '---:|' * len(names)]
    for kind in kinds:
        values = []
        for name in names:
            group = summaries.get(name, {}).get('accuracy_by_mistake_type', {}).get(kind)
            values.append(f"{group['accuracy']:.4f} (n={group['count']})" if group else 'N/A')
        lines.append('| ' + clean(kind) + ' | ' + ' | '.join(values) + ' |')
    lines += ['', '| Inspector | False-block rate | Median (s) | p95 (s) | Timeouts | Errors | Holds | Reviews |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in names:
        metric = summaries.get(name)
        if metric is None:
            lines.append(f'| {clean(name)} | N/A | N/A | N/A | N/A | N/A | N/A | N/A |')
        else:
            fbr = metric['false_block_rate']
            lines.append(f"| {clean(name)} | {fbr if fbr is not None else 'N/A'} | "
                         f"{metric['latency']['median_s']} | {metric['latency']['p95_s']} | "
                         f"{metric['timeout_count']} | {metric['error_count']} | "
                         f"{metric['status_counts'].get('hold', 0)} | {metric['status_counts'].get('needs_review', 0)} |")
    lines += ['', 'False-block = explicit BLOCK / expected PASS; HOLD and review counted separately.',
              'Accuracy = exact expected/actual decision-status agreement, including failed cases.',
              'Latency: inspector + validation + rules; p95 linearly interpolated.', '', '## Provenance']
    manifest_hashes = set()
    for name, path in results.items():
        provenance = Path(path).with_suffix('.provenance.json')
        if provenance.exists():
            data = json.loads(provenance.read_text(encoding='utf-8'))
            manifest_hashes.add(data.get('manifest_sha256'))
            lines += ['', f'### {clean(name)}', '', '```json', json.dumps(data, indent=2), '```']
        else:
            lines += ['', f'{clean(name)}: provenance unavailable (legacy CSV).']
    if len(manifest_hashes) > 1:
        lines += ['', '**Dataset manifests differ: these columns are not a controlled model comparison.**']
    for label, path in (('Recovery trials (P1)', recovery), ('Safety invariants (P3)', safety)):
        lines += ['', f'## {label}', '']
        if path:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            lines += ['Supplied teammate evidence; schema/coverage must be reviewed by its owner.',
                      '```json', json.dumps(data, indent=2), '```']
        else:
            lines += ['N/A — teammate output not supplied. No recovery/safety numbers inferred.']
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write('\n'.join(lines) + '\n')
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', action='append', required=True, metavar='NAME=CSV')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recovery', type=Path)
    parser.add_argument('--safety', type=Path)
    args = parser.parse_args()
    pairs = [value.split('=', 1) for value in args.result]
    if any(len(pair) != 2 for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
        parser.error('--result must use unique NAME=CSV pairs')
    generate_report(dict(pairs), args.output, recovery=args.recovery, safety=args.safety)


if __name__ == '__main__':
    main()
