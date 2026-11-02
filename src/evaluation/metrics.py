"""Metrics for independent labelled inspections (latencies in seconds)."""
from math import isfinite
from statistics import mean, median


def accuracy_by_mistake_type(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row['mistake_type'], []).append(row)
    return {
        kind: {'count': len(items), 'accuracy': sum(
            item['status'] == item['expected_status'] for item in items
        ) / len(items)}
        for kind, items in sorted(groups.items())
    }


def false_block_rate(rows):
    """BLOCK decisions / ground-truth PASS cases; None when no eligible cases.

    HOLD and NEEDS_REVIEW are reported separately by the runner, not as BLOCK.
    """
    correct = [row for row in rows if row['expected_status'] == 'pass']
    return (sum(row['status'] == 'block' for row in correct) / len(correct)
            if correct else None)


def latency_statistics(values):
    values = sorted(float(value) for value in values)
    if any(not isfinite(value) or value < 0 for value in values):
        raise ValueError('latencies must be finite, non-negative seconds')
    if not values:
        return {'count': 0, 'mean_s': None, 'median_s': None, 'p95_s': None}
    # Linear interpolation, equivalent to numpy's default percentile method.
    position = .95 * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return {'count': len(values), 'mean_s': mean(values),
            'median_s': median(values),
            'p95_s': values[lower] + (position - lower) * (values[upper] - values[lower])}
