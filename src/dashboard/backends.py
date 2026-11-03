"""Explicit integration seam; no inferred signatures and no silent fallback."""
import importlib


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
