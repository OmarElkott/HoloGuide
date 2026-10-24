import sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ollama import chat

from config import load_config
from vision.schemas import Observation

MODEL = "gemma3:4b"
IMAGES = ["scene_complete.png", "scene_missing_gasket.png", "scene_wrong_cover.png"]

PROMPT = """You are inspecting an assembly from an overhead camera image.
Part colors: base plate = grey, gasket = orange, cover = blue, bolts = red,
wrong cover = green.
Expected parts at this step: {expected}.
List which of the known parts you can see, which expected parts are missing,
and which visible parts do not belong. Give a confidence from 0 to 1 and one short sentence.
Respond only with JSON."""


def inspect(image_path: Path, expected: list[str]) -> Observation:
    resp = chat(
        model=MODEL,
        format=Observation.model_json_schema(),
        messages=[{"role": "user",
                   "content": PROMPT.format(expected=", ".join(expected)),
                   "images": [str(image_path)]}],
        options={"temperature": 0},
    )
    return Observation.model_validate_json(resp.message.content)


if __name__ == "__main__":
    cfg = load_config()
    expected = sorted(cfg.expected_parts(4))
    for name in IMAGES:
        path = ROOT / "outputs" / name
        t0 = time.perf_counter()
        obs = inspect(path, expected)
        print(f"\n{name}  ({time.perf_counter() - t0:.1f}s)")
        print(obs.model_dump_json(indent=2))