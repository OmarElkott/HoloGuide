from config import load_config
from vision.prompts import build_prompt, build_schema, color_name


def test_color_names_follow_the_yaml():
    cfg = load_config()
    names = {p.name: color_name(p.color_rgb) for p in cfg.parts}
    assert names == {"base_plate": "grey", "gasket": "orange", "cover": "blue",
                     "bolts": "red", "wrong_cover": "green"}


def test_prompt_lists_every_part_and_the_step():
    cfg = load_config()
    prompt = build_prompt(cfg, cfg.step(3))
    for part in cfg.parts:
        assert part.name in prompt
    assert "Step being checked: 3 of 4" in prompt
    assert "JSON only" in prompt


def test_prompt_does_not_leak_expected_parts_or_ground_truth():
    cfg = load_config()
    prompt = build_prompt(cfg, cfg.step(2)).lower()
    assert "expected" not in prompt
    assert "missing_parts" not in prompt


def test_prompt_differs_per_step():
    cfg = load_config()
    assert len({build_prompt(cfg, s) for s in cfg.steps}) == len(cfg.steps)


def test_schema_restricts_part_names_and_certainty():
    cfg = load_config()
    schema = build_schema(cfg)
    assert set(schema["properties"]["parts_detected"]["items"]["enum"]) == {p.name for p in cfg.parts}
    assert schema["properties"]["certainty"]["enum"] == ["clear", "partly_clear", "unclear"]
