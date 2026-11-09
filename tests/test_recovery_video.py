import csv
import json

import pytest
from PIL import Image

from src.simulation.recovery_video import export_recovery_video
from src.simulation.scenarios import NAMES


def fixture_trials(root):
    (root / 'provenance.json').write_text(json.dumps({'evidence': 'test-double'}))
    with (root / 'recovery.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['scenario', 'trial', 'decisions',
            'evidence', 'inspector', 'completed', 'error'])
        writer.writeheader()
        for name in NAMES:
            frames = root / name / '0' / 'frames'
            frames.mkdir(parents=True)
            Image.new('RGB', (640, 480), 'orange').save(frames / '001.png')
            writer.writerow(dict(scenario=name, trial=0, decisions='["block"]',
                evidence='test-double', inspector='Fixture', completed=False,
                error='Deliberate unsuccessful trial'))


def test_export_retains_failed_trials_and_source_identity(tmp_path):
    fixture_trials(tmp_path)
    result = export_recovery_video(tmp_path, tmp_path/'video.mp4', fps=5, seconds_per_frame=.2)
    assert result['frames'] == 4
    assert result['evidence'] == 'test-double'
    assert not result['audio'] and not result['screen_recording']
    assert len(result['inputs']) == 4
    assert len(result['video_sha256']) == 64
    with pytest.raises(FileExistsError):
        export_recovery_video(tmp_path, tmp_path/'video.mp4')


def test_export_rejects_incomplete_evidence(tmp_path):
    fixture_trials(tmp_path)
    (tmp_path/'wrong_cover/0/frames/001.png').rename(tmp_path/'displaced.png')
    with pytest.raises(ValueError, match='Frame/decision mismatch'):
        export_recovery_video(tmp_path, tmp_path/'video.mp4')
    assert not (tmp_path/'video.mp4').exists()


def test_export_rejects_conflicting_classification(tmp_path):
    fixture_trials(tmp_path)
    (tmp_path/'provenance.json').write_text('{"evidence":"real-inspector"}')
    with pytest.raises(ValueError, match='Conflicting evidence'):
        export_recovery_video(tmp_path, tmp_path/'video.mp4')
