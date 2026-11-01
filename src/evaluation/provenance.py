"""Reproducibility metadata without collecting credentials or environment secrets."""
import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect(manifest, config, inspector, prepared, *, model=None, endpoint=None):
    root = Path(__file__).resolve().parents[2]
    revision = subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}',
                               'rev-parse', 'HEAD'], cwd=root, text=True,
                              capture_output=True, check=False)
    dirty = subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}',
                            'status', '--porcelain'], cwd=root, text=True,
                           capture_output=True, check=False)
    packages = {}
    for package in ('pydantic', 'pyyaml', 'ollama', 'opencv-python', 'numpy'):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'manifest_path': str(Path(manifest).resolve()), 'manifest_sha256': sha256(manifest),
        'images': [{'path': str(path), 'sha256': sha256(path)} for _, path, _ in prepared],
        'configuration': config.model_dump(mode='json'),
        'inspector': f'{type(inspector).__module__}.{type(inspector).__qualname__}',
        'model': model, 'endpoint': endpoint,
        'code_revision': revision.stdout.strip() if revision.returncode == 0 else None,
        'working_tree_dirty': bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        'python': sys.version, 'platform': platform.platform(), 'packages': packages,
        'hardware': 'Not measured: record demo laptop CPU/GPU and Ollama model digest separately',
        'measurement_interval': 'inspect + Observation validation + deterministic rule evaluation; excludes image rendering and file writes',
    }
