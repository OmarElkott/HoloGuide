
import pytest

from src.config import load_config
from src.simulation.assembly_env import AssemblyEnv


@pytest.fixture
def env():
    simulation = AssemblyEnv(load_config())
    try:
        yield simulation
    finally:
        simulation.close()


def test_place_parts(env):
    env.place_part("base_plate")
    env.place_part("gasket")
    env.place_part("cover")
    env.place_part("bolts")

    assert env.parts_present() == [
        "base_plate", "gasket", "cover", "bolts"
    ]


def test_remove_gasket(env):
    env.place_part("base_plate")
    env.place_part("gasket")
    env.place_part("cover")

    env.remove_part("gasket")

    assert "gasket" not in env.parts_present()
    assert "cover" in env.parts_present()


def test_wrong_cover(env):
    env.place_part("base_plate")
    env.place_part("gasket")
    env.place_part("wrong_cover")

    assert "wrong_cover" in env.parts_present()
    assert "cover" not in env.parts_present()


def test_reset(env):
    env.place_part("base_plate")
    env.reset()

    assert env.parts_present() == []


def test_invalid_part(env):
    with pytest.raises(ValueError):
        env.place_part("invalid_part")
