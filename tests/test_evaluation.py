import csv
import json

import pytest

from src.evaluation.metrics import accuracy_by_mistake_type, false_block_rate, latency_statistics
from src.evaluation.runner import run
from src.vision.schemas import Observation


def test_metrics_denominators_and_empty_cases():
    rows = [dict(mistake_type='correct', expected_status='pass', status=status)
            for status in ('pass', 'block', 'hold', 'needs_review')]
    assert false_block_rate(rows) == .25
    assert accuracy_by_mistake_type(rows)['correct'] == {'count': 4, 'accuracy': .25}
    assert false_block_rate([]) is None
    assert accuracy_by_mistake_type([]) == {}
    assert latency_statistics([])['mean_s'] is None
    assert latency_statistics([1, 2, 3])['p95_s'] == pytest.approx(2.9)
    for invalid in (-1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            latency_statistics([invalid])


def manifest(tmp_path):
    frame = tmp_path / 'frame.json'
    frame.write_text(json.dumps({'mode': 'mock', 'visible_parts': ['base_plate']}))
    path = tmp_path / 'labels.csv'
    path.write_text('image_path,step_id,mistake_type,expected_status\nframe.json,1,correct,pass\n')
    return path


def test_mock_runner_writes_measured_results(tmp_path, assembly_config, mock_inspector):
    output = tmp_path / 'results.csv'
    rows, summary = run(manifest(tmp_path), mock_inspector, assembly_config, output)
    assert rows[0]['status'] == 'pass'
    assert rows[0]['latency_s'] >= 0
    assert summary['mock'] is True
    assert summary['false_block_rate'] == 0
    with output.open() as stream:
        assert len(list(csv.DictReader(stream))) == 1
    assert output.with_suffix('.summary.json').is_file()
    assert '| correct | 1 | 1.0000 |' in output.with_suffix('.tables.md').read_text()
    with pytest.raises(FileExistsError):
        run(tmp_path / 'labels.csv', mock_inspector, assembly_config, output)


def test_labels_are_not_passed_and_failure_is_retained(tmp_path, assembly_config):
    class Spy:
        def inspect(self, image_path, step):
            assert image_path.name == 'frame.json'
            assert set(step.model_dump()) == {'id', 'name', 'instruction', 'adds_parts'}
            raise TimeoutError('test failure')
    rows, summary = run(manifest(tmp_path), Spy(), assembly_config, tmp_path / 'results.csv')
    assert rows[0]['status'] == 'hold'
    assert 'TimeoutError' in rows[0]['error']
    assert summary['error_count'] == 1
    assert summary['accuracy_by_mistake_type']['correct']['accuracy'] == 0


def test_invalid_manifest_does_not_invoke_inspector(tmp_path, assembly_config, mock_inspector):
    path = manifest(tmp_path)
    path.write_text(path.read_text().replace(',1,', ',0,'))
    with pytest.raises(ValueError):
        run(path, mock_inspector, assembly_config, tmp_path / 'out.csv')
    assert mock_inspector.inspection_count == 0
