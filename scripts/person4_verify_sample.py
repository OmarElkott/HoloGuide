"""Verify P4 evaluation with the actual archived PyBullet frame in P2's notebook.

One missing-part sample only: NOT a final dataset, hardware benchmark or new
simulation capture. Uses existing real inspectors and reporting code unchanged.
"""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import load_config
from src.evaluation.runner import run
from src.evaluation.report import generate_report
from src.vision import factories


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'notebooks/hologuide_demo.ipynb'
    notebook = json.loads(source.read_text(encoding='utf-8'))
    code = ''.join(notebook['cells'][6]['source'])
    if 'render(["base_plate", "cover"]' not in code or 'config.step(3)' not in code:
        raise ValueError('Archived sample source changed; revalidate its identity before scoring')
    data = notebook['cells'][7]['outputs'][0]['data']['image/png']
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'frame.png').write_bytes(base64.b64decode(''.join(data)))
    manifest = args.output / 'labels.csv'
    manifest.write_text('image_path,step_id,mistake_type,expected_status\nframe.png,3,missing_part,block\n', encoding='utf-8')
    results, summaries = {}, {}
    for label, factory in [('OpenCV only', factories.cv), ('Hybrid', factories.hybrid)]:
        inspector = factory()
        output = args.output / (label.split()[0].lower() + '.csv')
        _, summaries[label] = run(manifest, inspector, load_config(), output)
        summaries[label]['gemma_calls'] = getattr(inspector, 'gemma_calls', None)
        results[label] = output
    generate_report(results, args.output / 'comparison.md')
    evidence = {'evidence': 'archived-render integration check only; n=1, not final evaluation',
                'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'source_cells': [6, 7], 'results': summaries}
    (args.output / 'verification.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
