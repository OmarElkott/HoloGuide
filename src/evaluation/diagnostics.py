"""Read P2's existing diagnostics; never infer timeout from HOLD alone."""
from src.vision.inspector import is_failure, is_timeout


def counters(inspector):
    result, seen = {}, set()
    def visit(item):
        if id(item) in seen:
            return
        seen.add(id(item))
        result[id(item)] = (item, getattr(item, 'inspection_count', None))
        for name in ('inner', 'gemma', 'cv', 'inspector'):
            child = getattr(item, name, None)
            if child is not None:
                visit(child)
    visit(inspector)
    return result


def outcome(observation, before):
    statuses = []
    for item, count in before.values():
        # Hybrid can skip Gemma: don't reuse a previous call's stale failure.
        if count is not None and getattr(item, 'inspection_count', None) != count:
            statuses.append(str(getattr(item, 'last_status', 'unknown')))
    timed_out = is_timeout(observation) or 'timeout' in statuses
    failed = is_failure(observation) or timed_out or any(
        status in {'error', 'parse_failure', 'gemma_unavailable'} for status in statuses)
    return timed_out, observation.rationale if failed else '', ','.join(statuses)
