"""Export a silent, captioned selection of saved recovery trials (not live capture)."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src.simulation.scenarios import NAMES


def export_recovery_video(source, output, *, trial=0, fps=20, seconds_per_frame=2.0):
    """Preserve every decision from one numbered trial of each scenario.

    Evidence classification is copied, never inferred from successful outcomes.
    This export does not certify the supplied inspector or model performance.
    """
    source, output = Path(source), Path(output)
    if output.suffix.lower() != '.mp4':
        raise ValueError('Output must have .mp4 extension')
    if type(trial) is not int or trial < 0:
        raise ValueError('trial must be a nonnegative integer')
    if type(fps) is not int or not 1 <= fps <= 60:
        raise ValueError('fps must be an integer in [1, 60]')
    if not math.isfinite(seconds_per_frame) or not 0 < seconds_per_frame <= 30:
        raise ValueError('seconds_per_frame must be in (0, 30]')
    metadata_path = output.with_suffix('.json')
    if output.exists() or metadata_path.exists():
        raise FileExistsError(output)
    provenance_path = source / 'provenance.json'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    evidence = provenance.get('evidence')
    if evidence not in {'real-inspector', 'test-double', 'unverified'}:
        raise ValueError('Missing or invalid evidence classification')
    csv_path = source / 'recovery.csv'
    with csv_path.open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    scenes, inputs = [], []
    for name in NAMES:
        matches = [row for row in rows if row['scenario'] == name and row['trial'] == str(trial)]
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one {name} trial {trial}')
        row = matches[0]
        if row['evidence'] != evidence:
            raise ValueError('Conflicting evidence classifications')
        decisions = json.loads(row['decisions'])
        paths = sorted((source / name / str(trial) / 'frames').glob('*.png'))
        if not paths or len(paths) != len(decisions):
            raise ValueError(f'Frame/decision mismatch: {name}')
        for index, (path, decision) in enumerate(zip(paths, decisions), 1):
            if decision not in {'pass', 'block', 'hold', 'needs_review'}:
                raise ValueError(f'Unknown decision: {decision}')
            scenes.append((path, name, index, decision, row))
            inputs.append({'path': path.relative_to(source).as_posix(),
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    try:
        font = ImageFont.truetype('arial.ttf', 23)
    except OSError:
        font = ImageFont.load_default()
    output.parent.mkdir(parents=True, exist_ok=True)
    repeats = max(1, round(fps * seconds_per_frame))
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*'mp4v'), fps, (960, 760))
    if not writer.isOpened():
        writer.release()
        raise RuntimeError('Local MP4 encoder unavailable')
    try:
        for path, name, index, decision, row in scenes:
            canvas = Image.new('RGB', (960, 760), '#142b41')
            with Image.open(path) as frame:
                canvas.paste(frame.convert('RGB').resize((800, 600)), (80, 65))
            draw = ImageDraw.Draw(canvas)
            draw.text((20, 8), f'SAVED TRIAL | {evidence} | silent replay, not live capture', font=font, fill='#ffd06a')
            draw.text((20, 36), f'{name} | trial {trial} | observation {index} | {decision.upper()}', font=font, fill='white')
            draw.text((20, 675), f"Inspector wrapper: {row['inspector']} | completed: {row['completed']}", font=font, fill='white')
            draw.text((20, 708), 'Error: ' + (row['error'] or 'none')[:80], font=font, fill='white')
            encoded = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
            for _ in range(repeats):
                writer.write(encoded)
    finally:
        writer.release()
    reader = cv2.VideoCapture(str(output))
    decoded = 0
    try:
        while True:
            ok, frame = reader.read()
            if not ok:
                break
            if frame.shape[:2] != (760, 960):
                raise RuntimeError('Unexpected decoded dimensions')
            decoded += 1
    finally:
        reader.release()
    if decoded != len(scenes) * repeats:
        raise RuntimeError('Video frame count verification failed')
    report = dict(evidence=evidence, trial=trial, scenarios=list(NAMES), audio=False,
                  screen_recording=False, frames=decoded, fps=fps, duration_s=decoded/fps,
                  timing='Fixed presentation duration; not measured inference time',
                  source=str(source.resolve()), inputs=inputs,
                  csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                  provenance_sha256=hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
                  video_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    metadata_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--trial', type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(export_recovery_video(**vars(args)), indent=2))


if __name__ == '__main__':
    main()
