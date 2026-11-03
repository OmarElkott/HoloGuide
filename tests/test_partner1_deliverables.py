"""Verification of measured rendering and explicitly synthetic video assets."""
import json
import pytest
from PIL import Image
from src.simulation.benchmark import benchmark, package_version
from src.simulation.export_video import export_video


def test_benchmark_measures_and_protects_previous_results(tmp_path):
    result=benchmark(tmp_path/'bench',samples=3)
    assert result['samples']==3
    assert result['median_s']>0
    assert result['p95_s']>=result['median_s']
    assert result['identical_frames']
    assert (tmp_path/'bench/benchmark.json').exists()
    with pytest.raises(FileExistsError):
        benchmark(tmp_path/'bench',samples=3)
    assert package_version('hologuide-no-such-distribution').startswith('unknown')


def test_video_is_decodable_and_labeled(tmp_path):
    folder=tmp_path/'rehearsal'
    frames=folder/'correct/frames'
    frames.mkdir(parents=True)
    Image.new('RGB',(640,480),'orange').save(frames/'001.png')
    (folder/'rehearsal.json').write_text(json.dumps({'evidence':'test-double',
        'results':[{'scenario':'correct','decisions':['pass']}]}))
    result=export_video(folder,tmp_path/'demo.mp4',seconds_per_step=.2,fps=10)
    assert result['frames']==2
    assert result['duration_s']==.2
    assert result['evidence']=='test-double'
    assert not result['screen_recording']
    with pytest.raises(FileExistsError):
        export_video(folder,tmp_path/'demo.mp4')


def test_video_rejects_invalid_evidence_before_writing(tmp_path):
    (tmp_path/'rehearsal.json').write_text(json.dumps({'evidence':'real-inspector','results':[]}))
    with pytest.raises(ValueError,match='explicitly labeled'):
        export_video(tmp_path,tmp_path/'bad.mp4')
    assert not (tmp_path/'bad.mp4').exists()
