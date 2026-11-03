import csv
import json

import pytest

from src.evaluation.report import generate_report, read_results
from src.evaluation.runner import run


def test_saved_csv_report_no_inspection_and_unavailable_models(tmp_path):
    path = tmp_path / 'saved.csv'
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['mistake_type', 'expected_status', 'status', 'latency_s', 'error'])
        writer.writeheader()
        for status, latency, error in [('block', 2, ''), ('hold', 4, 'TimeoutError: test')]:
            writer.writerow(dict(mistake_type='correct', expected_status='pass', status=status, latency_s=latency, error=error))
    output = tmp_path / 'report.md'
    summary = generate_report({'Mock validation': path}, output)['Mock validation']
    assert summary['false_block_rate'] == .5
    assert summary['timeout_count'] == 1
    assert summary['latency']['median_s'] == 3
    text = output.read_text()
    assert '| Gemma only | N/A' in text
    assert 'legacy CSV' in text
    assert 'Recovery trials (P1)' in text
    assert 'No recovery/safety numbers inferred' in text
    with pytest.raises(FileExistsError):
        generate_report({'Mock validation': path}, output)


def test_invalid_saved_results_rejected(tmp_path):
    path = tmp_path / 'bad.csv'
    path.write_text('mistake_type,expected_status,status,latency_s\ncorrect,pass,pass,nan\n')
    with pytest.raises(ValueError):
        read_results(path)


def test_provenance_identifies_dataset_config_revision_and_timeouts(tmp_path, assembly_config):
    path = tmp_path / 'labels.csv'
    (tmp_path / 'frame.json').write_text('{}')
    path.write_text('image_path,step_id,mistake_type,expected_status\nframe.json,1,correct,pass\n')
    class TimeoutInspector:
        def inspect(self, image_path, step):
            raise TimeoutError('test timeout')
    output = tmp_path / 'run.csv'
    rows, summary = run(path, TimeoutInspector(), assembly_config, output, model='test-only')
    assert rows[0]['timed_out']
    assert summary['timeout_count'] == 1
    provenance = json.loads(output.with_suffix('.provenance.json').read_text())
    assert len(provenance['manifest_sha256']) == 64
    assert len(provenance['images'][0]['sha256']) == 64
    assert provenance['configuration']['name'] == assembly_config.name
    assert provenance['code_revision']
    assert provenance['model'] == 'test-only'
    assert generate_report({'Test timeout': output}, tmp_path / 'tables.md')['Test timeout']['timeout_count'] == 1
