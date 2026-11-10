"""Explicit integration seam; no inferred signatures and no silent fallback."""
import importlib
from urllib.parse import urlsplit


class StepIdInspectorAdapter:
    """Roadmap P2 interface inspect(path, step_id) -> actual P3 StepSpec interface."""
    def __init__(self, inspector):
        self.inspector = inspector

    def inspect(self, image_path, step):
        return self.inspector.inspect(str(image_path), step.id)


def load_factory(spec):
    """Load a configured team factory 'module:function' without guessing constructors.

    Factory returns DashboardSession(mode='real', environment=..., inspector=...).
    Use StepIdInspectorAdapter if the teammate implements the roadmap P2 API.
    """
    module, name = spec.split(':', 1)
    factory = getattr(importlib.import_module(module), name)
    session = factory()
    if session.mode != 'real':
        session.close()
        raise ValueError('Real backend factory returned a non-real session')
    return session


def inspector_configuration(inspector):
    """Describe actual P2 object settings; localhost is not offline evidence."""
    nodes, seen = [], set()
    def visit(item):
        if id(item) in seen:
            return
        seen.add(id(item))
        nodes.append(item)
        for name in ('inner', 'gemma', 'cv', 'inspector'):
            child = getattr(item, name, None)
            if child is not None:
                visit(child)
    visit(inspector)
    model = next((getattr(n, 'model') for n in nodes if getattr(n, 'model', None)), None)
    endpoint = None
    if model:
        import os
        endpoint = next((getattr(n, '_host') for n in nodes if getattr(n, '_host', None)), None)
        endpoint = str(endpoint or os.environ.get('OLLAMA_HOST') or 'http://localhost:11434')
        parsed = urlsplit(endpoint if '://' in endpoint else 'http://' + endpoint)
        try:
            host = f'[{parsed.hostname}]' if ':' in (parsed.hostname or '') else parsed.hostname
            endpoint = f'{parsed.scheme}://{host}' + (f':{parsed.port}' if parsed.port else '')
            local = parsed.hostname in {'localhost', '127.0.0.1', '::1'}
            connection = 'Local model endpoint configured' if local else 'Non-loopback model endpoint configured'
        except ValueError:
            endpoint, connection = '(invalid endpoint configuration)', 'Invalid model endpoint'
    else:
        connection = 'Image processing only; no model endpoint in this inspector'
    from dataclasses import asdict, is_dataclass
    components = []
    for node in nodes:
        detail = {'class': f'{type(node).__module__}.{type(node).__qualname__}'}
        for name in ('prompt_version', 'crop', 'timeout_s', 'explain'):
            if hasattr(node, name):
                detail[name] = getattr(node, name)
        if is_dataclass(getattr(node, 'settings', None)):
            detail['cv_settings'] = asdict(node.settings)
        components.append(detail)
    return {'inspector': type(inspector).__name__, 'model': model, 'endpoint': endpoint,
            'components': components,
            'connection': connection, 'offline_evidence': 'Wi-Fi-off rehearsal not verified here'}
