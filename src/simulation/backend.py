"""Real dashboard factory: PyBullet plus an explicitly configured P2 inspector."""
import os
from src.config import load_config
from src.dashboard.session import DashboardSession
from src.simulation.assembly_env import AssemblyEnv
from src.simulation.runner import load_inspector


def create_session():
    spec = os.environ.get('HOLOGUIDE_INSPECTOR_FACTORY')
    if not spec:
        raise ValueError('Set HOLOGUIDE_INSPECTOR_FACTORY to the P2 module:function factory')
    inspector = load_inspector(spec, os.environ.get('HOLOGUIDE_STEP_ID_INTERFACE') == '1')
    env = AssemblyEnv(load_config(), seed=2026)
    try:
        return DashboardSession(environment=env, inspector=inspector, mode='real',
                                endpoint=os.environ.get('HOLOGUIDE_MODEL_ENDPOINT'),
                                model=os.environ.get('HOLOGUIDE_MODEL_NAME'))
    except Exception:
        env.close()
        raise
