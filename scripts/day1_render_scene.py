import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pybullet as p
from PIL import Image

from config import load_config

OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

# name -> (shape, half-extents or (radius, height), position)
BOLT_XY = [(0.10, 0.10), (-0.10, 0.10), (0.10, -0.10), (-0.10, -0.10)]


def add_box(half, pos, rgb):
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=[*rgb, 1])
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half)
    return p.createMultiBody(0, col, vis, basePosition=pos)


def add_cylinder(radius, height, pos, rgb):
    vis = p.createVisualShape(p.GEOM_CYLINDER, radius=radius, length=height, rgbaColor=[*rgb, 1])
    col = p.createCollisionShape(p.GEOM_CYLINDER, radius=radius, height=height)
    return p.createMultiBody(0, col, vis, basePosition=pos)


def build_part(cfg, name):
    rgb = cfg.part(name).color_rgb
    if name == "base_plate":
        add_box([0.18, 0.18, 0.01], [0, 0, 0.01], rgb)
    elif name == "gasket":
        add_box([0.16, 0.16, 0.003], [0, 0, 0.023], rgb)      # wide: rim stays visible
    elif name in ("cover", "wrong_cover"):
        add_box([0.12, 0.12, 0.015], [0, 0, 0.041], rgb)
    elif name == "bolts":
        for x, y in BOLT_XY:
            add_cylinder(0.012, 0.03, [x * 1.35, y * 1.35, 0.059], rgb)


def render(cfg, parts_present, filename):
    p.connect(p.DIRECT)
    p.resetSimulation()
    p.setGravity(0, 0, -9.81)
    plane_vis = p.createVisualShape(p.GEOM_BOX, halfExtents=[1, 1, 0.001], rgbaColor=[0.92, 0.92, 0.92, 1])
    p.createMultiBody(0, baseVisualShapeIndex=plane_vis, basePosition=[0, 0, -0.001])

    for name in parts_present:
        build_part(cfg, name)

    c = cfg.camera
    view = p.computeViewMatrixFromYawPitchRoll(c.target, c.distance, c.yaw, c.pitch, 0, 2)
    proj = p.computeProjectionMatrixFOV(60, c.width / c.height, 0.05, 3.0)
    _, _, rgba, _, _ = p.getCameraImage(c.width, c.height, view, proj,
                                        renderer=p.ER_TINY_RENDERER)
    img = np.reshape(np.array(rgba, dtype=np.uint8), (c.height, c.width, 4))[:, :, :3]
    Image.fromarray(img).save(OUT / filename)
    p.disconnect()
    print("saved", OUT / filename)


if __name__ == "__main__":
    cfg = load_config()
    render(cfg, ["base_plate", "gasket", "cover", "bolts"], "scene_complete.png")
    render(cfg, ["base_plate", "cover", "bolts"], "scene_missing_gasket.png")
    render(cfg, ["base_plate", "gasket", "wrong_cover", "bolts"], "scene_wrong_cover.png")