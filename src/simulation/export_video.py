"""Export a labeled, silent rehearsal MP4 using already installed OpenCV.

This is a storyboard asset, not a screen recording or an AI demonstration.
"""
import argparse
import json
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from src.simulation.scenarios import NAMES


def export_video(rehearsal, output, *, seconds_per_step=1.5, fps=20):
    rehearsal, output = Path(rehearsal), Path(output)
    if output.suffix.lower() != '.mp4':
        raise ValueError('Output must have .mp4 extension')
    if not math.isfinite(seconds_per_step) or not 0 < seconds_per_step <= 30:
        raise ValueError('seconds_per_step must be in (0, 30]')
    if not isinstance(fps,int) or not 1 <= fps <= 60:
        raise ValueError('fps must be an integer in [1, 60]')
    if output.exists() or output.with_suffix('.json').exists():
        raise FileExistsError(output)
    data=json.loads((rehearsal/'rehearsal.json').read_text(encoding='utf-8'))
    if data.get('evidence') != 'test-double':
        raise ValueError('This exporter accepts explicitly labeled rehearsal evidence only')
    try:
        font=ImageFont.truetype('arial.ttf',24)
    except OSError:
        font=ImageFont.load_default()
    scenes=[]
    for result in data['results']:
        name=result['scenario']
        if name not in NAMES:
            raise ValueError(f'Unknown scenario: {name}')
        paths=sorted((rehearsal/name/'frames').glob('*.png'))
        if len(paths) != len(result['decisions']) or not paths:
            raise ValueError(f'Frame/decision mismatch for {name}')
        for index,(path,decision) in enumerate(zip(paths,result['decisions']),1):
            with Image.open(path) as source:
                canvas=Image.new('RGB',(960,720),'#142b41')
                canvas.paste(source.convert('RGB').resize((800,600)),(80,55))
            draw=ImageDraw.Draw(canvas)
            draw.text((24,14),'SCRIPTED REHEARSAL - NOT AI PERFORMANCE',fill='#ffd06a',font=font)
            draw.text((24,670),f'{name} | step in script {index} | decision: {decision}',fill='white',font=font)
            scenes.append(cv2.cvtColor(np.asarray(canvas),cv2.COLOR_RGB2BGR))
    if not scenes:
        raise ValueError('No rehearsal scenes')
    output.parent.mkdir(parents=True,exist_ok=True)
    repeats=max(1,round(seconds_per_step*fps))
    writer=cv2.VideoWriter(str(output),cv2.VideoWriter_fourcc(*'mp4v'),fps,(960,720))
    if not writer.isOpened():
        raise RuntimeError('Local MP4 encoder unavailable; no downloads attempted')
    try:
        for scene in scenes:
            for _ in range(repeats):
                writer.write(scene)
    finally:
        writer.release()
    reader=cv2.VideoCapture(str(output))
    decoded=0
    try:
        while True:
            ok,frame=reader.read()
            if not ok:
                break
            if frame.shape[:2] != (720,960):
                raise RuntimeError('Unexpected decoded dimensions')
            decoded+=1
    finally:
        reader.release()
    expected=len(scenes)*repeats
    if decoded != expected:
        raise RuntimeError(f'Video verification failed: {decoded}/{expected} frames')
    report=dict(evidence='test-double',audio=False,screen_recording=False,frames=decoded,
                fps=fps,duration_s=decoded/fps,width=960,height=720,
                warning='Prewritten observations; no real AI performance. Not the final competition video.')
    output.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rehearsal',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(export_video(**vars(args)),indent=2))


if __name__ == '__main__':
    main()
