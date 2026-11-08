import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import load_config
from control.rules import RuleContext, evaluate
from vision.gemma_inspector import GemmaInspector
from vision.inspector import is_failure
from vision.schemas import DecisionStatus

ALL = ["base_plate", "gasket", "cover", "bolts"]


def reply(parts, certainty="clear", why="ok"):
    return json.dumps({"parts_detected": parts, "certainty": certainty, "rationale": why})


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(message=SimpleNamespace(content=item))

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "frame.png"
    p.write_bytes(b"\x89PNG not a real image; the fake client never opens it")
    return p


def decide(cfg, obs, step_id):
    step = cfg.step(step_id)
    return evaluate(obs, step, cfg.confidence_threshold, context=RuleContext.from_config(cfg, step))


def test_correct_assembly_passes_the_rules(cfg, image):
    insp = GemmaInspector(cfg, client=FakeClient(reply(ALL)))
    obs = insp.inspect(image, cfg.step(4))
    assert obs.parts_detected == sorted(ALL) and obs.missing_parts == [] and obs.confidence == 0.9
    assert insp.last_status == "ok"
    assert decide(cfg, obs, 4).status is DecisionStatus.PASS


def test_missing_gasket_is_derived_in_code_and_blocks(cfg, image):
    insp = GemmaInspector(cfg, client=FakeClient(reply(["base_plate", "cover", "bolts"])))
    obs = insp.inspect(image, cfg.step(4))
    assert obs.missing_parts == ["gasket"]
    assert decide(cfg, obs, 4).status is DecisionStatus.BLOCK


def test_wrong_cover_is_reported_and_blocks(cfg, image):
    insp = GemmaInspector(cfg, client=FakeClient(reply(["base_plate", "gasket", "wrong_cover"])))
    obs = insp.inspect(image, cfg.step(3))
    assert obs.wrong_parts == ["wrong_cover"]
    decision = decide(cfg, obs, 3)
    assert decision.status is DecisionStatus.BLOCK and "Wrong cover" in decision.message


def test_partly_clear_goes_to_review_not_a_hard_block(cfg, image):
    insp = GemmaInspector(cfg, client=FakeClient(reply(["base_plate", "cover"], "partly_clear")))
    obs = insp.inspect(image, cfg.step(3))
    assert obs.confidence == 0.6 and obs.missing_parts == []
    assert decide(cfg, obs, 3).status is DecisionStatus.NEEDS_REVIEW


def test_request_uses_schema_image_keep_alive_and_temperature_zero(cfg, image):
    client = FakeClient(reply(["base_plate"]))
    GemmaInspector(cfg, client=client, keep_alive="10m").inspect(image, 1)
    call = client.calls[0]
    assert call["model"] == "gemma3:4b" and call["keep_alive"] == "10m"
    assert call["options"]["temperature"] == 0
    assert call["messages"][0]["images"] == [str(image)]
    assert call["format"]["required"] == ["parts_detected", "certainty", "rationale"]


def test_retry_once_then_succeed(cfg, image):
    client = FakeClient("not json at all", reply(["base_plate"]))
    insp = GemmaInspector(cfg, client=client)
    obs = insp.inspect(image, 1)
    assert len(client.calls) == 2 and insp.last_status == "retried_ok"
    assert not is_failure(obs)


@pytest.mark.parametrize("bad", ["not json", "", "{}", reply(["flux_capacitor"]),
                                 json.dumps({"parts_detected": [], "certainty": "sure", "rationale": ""})])
def test_two_bad_replies_give_safe_zero_confidence_observation(cfg, image, bad):
    client = FakeClient(bad, bad)
    insp = GemmaInspector(cfg, client=client)
    obs = insp.inspect(image, 2)
    assert len(client.calls) == 2                  # exactly one retry
    assert obs.confidence == 0.0 and is_failure(obs)
    assert insp.last_status == "parse_failure"
    assert decide(cfg, obs, 2).status is DecisionStatus.HOLD


def test_client_exception_never_escapes(cfg, image):
    insp = GemmaInspector(cfg, client=FakeClient(ConnectionError("down"), ConnectionError("down")))
    obs = insp.inspect(image, 1)
    assert obs.confidence == 0.0 and insp.last_status == "error"
    assert "ConnectionError" in obs.rationale


def test_missing_image_does_not_call_the_model(cfg, tmp_path):
    client = FakeClient()
    insp = GemmaInspector(cfg, client=client)
    obs = insp.inspect(tmp_path / "nope.png", 1)
    assert client.calls == [] and obs.confidence == 0.0


def test_accepts_stepspec_and_step_id_and_rejects_bad_ids_safely(cfg, image):
    insp = GemmaInspector(cfg, client=FakeClient(reply(["base_plate"]), reply(["base_plate"]), reply([])))
    assert insp.inspect(image, cfg.step(1)).confidence == 0.9
    assert insp.inspect(image, 1).confidence == 0.9
    assert insp.inspect(image, 99).confidence == 0.0       # ValueError is contained


def test_warm_up_uses_keep_alive(cfg):
    client = FakeClient()
    info = GemmaInspector(cfg, client=client, keep_alive="45m").warm_up()
    assert info["ok"] and client.calls[0][1]["keep_alive"] == "45m"


def test_image_downscale_sends_bytes(cfg, tmp_path):
    from PIL import Image
    path = tmp_path / "big.png"
    Image.new("RGB", (640, 480), "gray").save(path)
    client = FakeClient(reply(["base_plate"]))
    GemmaInspector(cfg, client=client, max_image_side=256).inspect(path, 1)
    assert isinstance(client.calls[0]["messages"][0]["images"][0], bytes)


# ---- VIS-10: crop to the assembly + prompt versions ---------------------------

def test_crop_enlarges_the_assembly_and_keeps_empty_scenes(tmp_path):
    from PIL import Image
    from vision.gemma_inspector import crop_to_assembly
    scene = Image.new("RGB", (640, 480), (220, 220, 220))
    scene.paste((40, 70, 220), (290, 200, 350, 260))           # a small blue part
    cropped = crop_to_assembly(scene)
    assert cropped.width == cropped.height >= 448                # square, upscaled
    import numpy as np
    pixels = np.asarray(cropped)
    blue = int(((pixels[..., 2] > 150) & (pixels[..., 0] < 120)).sum())
    assert blue / pixels[..., 0].size > 3600 / (640 * 480)
    empty = Image.new("RGB", (640, 480), (220, 220, 220))
    assert crop_to_assembly(empty).size == (640, 480)


def test_crop_sends_png_bytes_and_prompt_version_is_used(cfg, tmp_path):
    from PIL import Image
    path = tmp_path / "real.png"
    Image.new("RGB", (640, 480), (220, 220, 220)).save(path)
    client = FakeClient(reply(["base_plate"]))
    inspector = GemmaInspector(cfg, client=client, crop=True, prompt_version="v2")
    inspector.inspect(path, 1)
    message = client.calls[0]["messages"][0]
    assert isinstance(message["images"][0], bytes) and message["images"][0][:4] == b"\x89PNG"
    from vision.prompts import build_prompt
    assert message["content"] == build_prompt(cfg, cfg.step(1), "v2")


def v2_reply(grey=False, orange=False, blue=False, green=False, red=False, certainty="clear"):
    return json.dumps({"dark_grey_plate": grey, "orange_layer": orange, "blue_square": blue,
                       "green_square": green, "red_pegs": red, "certainty": certainty,
                       "rationale": "colours"})


def test_v2_answers_map_to_parts_and_rules_decide(cfg, image):
    client = FakeClient(v2_reply(grey=True, orange=True, green=True))
    obs = GemmaInspector(cfg, client=client, prompt_version="v2").inspect(image, 3)
    assert obs.parts_detected == ["base_plate", "gasket", "wrong_cover"]
    assert obs.wrong_parts == ["wrong_cover"] and obs.missing_parts == ["cover"]
    assert decide(cfg, obs, 3).status == DecisionStatus.BLOCK
    empty = GemmaInspector(cfg, client=FakeClient(v2_reply()), prompt_version="v2").inspect(image, 1)
    assert empty.parts_detected == [] and decide(cfg, empty, 1).status == DecisionStatus.BLOCK


def test_v2_malformed_answer_retries_then_holds(cfg, image):
    bad = json.dumps({"dark_grey_plate": "yes", "certainty": "clear", "rationale": ""})
    client = FakeClient(bad, bad)
    inspector = GemmaInspector(cfg, client=client, prompt_version="v2")
    obs = inspector.inspect(image, 1)
    assert len(client.calls) == 2 and inspector.last_status == "parse_failure"
    assert is_failure(obs) and decide(cfg, obs, 1).status == DecisionStatus.HOLD


def test_unknown_prompt_version_rejected_at_construction(cfg):
    with pytest.raises(ValueError):
        GemmaInspector(cfg, client=FakeClient(), prompt_version="v9")
