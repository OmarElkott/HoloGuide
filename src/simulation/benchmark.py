"""Measure local render-to-PNG latency, not model or GPU inference latency."""
import argparse
import hashlib
import json
import platform
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from time import perf_counter
import numpy as np
from src.config import load_config
from src.simulation.assembly_env import AssemblyEnv


def package_version(name):
    """Locally copied modules may be importable without distribution metadata."""
    try:
        return version(name)
    except PackageNotFoundError:
        return 'unknown (distribution metadata unavailable)'


def benchmark(output, *, samples=30, seed=2026):
    if samples < 2:
        raise ValueError('At least two samples are required')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = load_config()
    times, hashes = [], []
    with AssemblyEnv(config, seed=seed, randomize=True) as env:
        for part in sorted(config.expected_parts(4)):
            env.place_part(part)
        env.render_to_file(str(output / 'warmup.png'))
        for index in range(samples):
            target = output / f'{index:03d}.png'
            start = perf_counter()
            env.render_to_file(str(target))
            times.append(perf_counter() - start)
            hashes.append(hashlib.sha256(target.read_bytes()).hexdigest())
    result = dict(measurement='TinyRenderer plus PNG encoding and filesystem write; no AI inference',
        samples=samples, seed=seed, median_s=float(np.median(times)),
        p95_s=float(np.percentile(times,95)), identical_frames=len(set(hashes)) == 1,
        python=platform.python_version(), platform=platform.platform(),
        versions={name:package_version(name) for name in ('pybullet','numpy','pillow')},
        config=config.model_dump(mode='json'), durations_s=times, sha256=hashes)
    (output/'benchmark.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=30)
    args=parser.parse_args()
    print(json.dumps(benchmark(**vars(args)),indent=2))


if __name__ == '__main__':
    main()
