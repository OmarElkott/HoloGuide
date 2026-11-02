"""Generate separate PNG inputs and evaluation CSV labels, offline."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from src.config import load_config
from src.simulation.assembly_env import AssemblyEnv


def generate(output, *, config=None, seed=0, randomize=False, correct=80, per_fault=40):
    config = config or load_config()
    output = Path(output)
    if correct < 1 or per_fault < 1:
        raise ValueError('Counts must be positive')
    # Exclusive creation protects previous datasets and partial runs.
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for kind, count in [('correct', correct), ('missing_part', per_fault),
                        ('wrong_part', per_fault), ('wrong_order', per_fault)]:
        for i in range(count):
            # No future part exists at the final step: wrong-order cases use 1..3.
            step = 1 + i % (len(config.steps) - (kind == 'wrong_order'))
            present = sorted(config.expected_parts(step))
            if kind == 'missing_part':
                present.remove(config.step(step).adds_parts[0])
            elif kind == 'wrong_part':
                present.append('wrong_cover')
                if 'cover' in present:
                    present.remove('cover')
            elif kind == 'wrong_order':
                present.extend(config.step(step + 1).adds_parts)
            sample_seed = seed + len(rows)
            image_path = f'images/{len(rows):06d}.png'
            with AssemblyEnv(config, seed=sample_seed, randomize=randomize) as env:
                for part in present:
                    env.place_part(part)
                env.render_to_file(str(output / image_path))
            rows.append(dict(image_path=image_path, step_id=step, mistake_type=kind,
                             expected_status='pass' if kind == 'correct' else 'block',
                             seed=sample_seed, parts_present=json.dumps(present),
                             sha256=hashlib.sha256((output / image_path).read_bytes()).hexdigest()))
    with (output / 'labels.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'dataset.json').write_text(json.dumps(dict(seed=seed, randomize=randomize,
        count=len(rows), config=config.model_dump(mode='json'),
        note='Ground truth is evaluation-only. Static images cannot prove historical action order; wrong_order means a visible future part.'), indent=2), encoding='utf-8')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--randomize', action='store_true')
    args = parser.parse_args()
    print(f'Generated {len(generate(**vars(args)))} images')


if __name__ == '__main__':
    main()
