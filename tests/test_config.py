import pytest
from config import AssemblyConfig, load_config


def test_loads_and_has_four_steps():
    cfg = load_config()
    assert len(cfg.steps) == 4


def test_expected_parts_accumulate():
    cfg = load_config()
    assert cfg.expected_parts(1) == {"base_plate"}
    assert cfg.expected_parts(3) == {"base_plate", "gasket", "cover"}
    assert cfg.expected_parts(4) == {"base_plate", "gasket", "cover", "bolts"}


def test_unknown_part_is_rejected():
    cfg = load_config().model_dump()
    cfg["steps"][0]["adds_parts"] = ["does_not_exist"]
    with pytest.raises(ValueError):
        AssemblyConfig.model_validate(cfg)