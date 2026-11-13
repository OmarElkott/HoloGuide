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


def code_identity(root):
    """Identify runtime source even in ZIP exports or hosts without Git."""
    root = Path(root)
    digest = hashlib.sha256()
    paths = [*root.glob('src/**/*.py'), *root.glob('config/*.yaml')]
    paths += [path for path in (root / 'requirements.txt',) if path.is_file()]
    for path in sorted(paths, key=lambda path: path.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode('utf-8')
        content = path.read_bytes().replace(b'\r\n', b'\n')
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(len(content).to_bytes(8, 'big') + content)
    identity = {'code_revision': None, 'working_tree_dirty': None,
                'source_sha256': digest.hexdigest(), 'revision_status': 'unavailable'}
    # Do not accidentally attribute a ZIP to an enclosing, unrelated repository.
    if not (root / '.git').exists():
        return identity
    try:
        def git(*args):
            return subprocess.run(['git', '-c', f'safe.directory={root.as_posix()}', *args],
                                  cwd=root, text=True, capture_output=True,
                                  check=False, timeout=5)
        revision = git('rev-parse', '--verify', 'HEAD')
        dirty = git('status', '--porcelain')
    except (OSError, subprocess.TimeoutExpired):
        return identity
    if revision.returncode == 0:
        identity.update(code_revision=revision.stdout.strip(), revision_status='git')
    if dirty.returncode == 0:
        identity['working_tree_dirty'] = bool(dirty.stdout.strip())
    return identity


def collect(manifest, config, inspector, prepared, *, model=None, endpoint=None):
    root = Path(__file__).resolve().parents[2]
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
        **code_identity(root),
        'python': sys.version, 'platform': platform.platform(), 'packages': packages,
        'hardware': 'Not measured: record demo laptop CPU/GPU and Ollama model digest separately',
        'measurement_interval': 'inspect + Observation validation + deterministic rule evaluation; excludes image rendering and file writes',
    }
