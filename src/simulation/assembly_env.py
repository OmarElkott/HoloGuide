
from pathlib import Path

import numpy as np
import pybullet as p
from PIL import Image

from src.config import AssemblyConfig


class AssemblyEnv:
    """Virtual manufacturing environment using PyBullet."""

    def __init__(
        self,
        config: AssemblyConfig,
        seed: int | None = None,
        randomize: bool = False,
    ):
        self.config = config
        self.seed = seed
        self.randomize = randomize
        self.client = p.connect(p.DIRECT)
        self._parts = {}
        self._foreign = {}
        self.reset()

    def reset(self) -> None:
        """Reset the simulation to an empty workstation."""
        p.resetSimulation(physicsClientId=self.client)
        p.setGravity(0, 0, -9.81, physicsClientId=self.client)

        floor = p.createVisualShape(
            p.GEOM_BOX,
            halfExtents=[1, 1, 0.001],
            rgbaColor=[0.92, 0.92, 0.92, 1],
            physicsClientId=self.client,
        )

        p.createMultiBody(
            baseMass=0,
            baseVisualShapeIndex=floor,
            basePosition=[0, 0, -0.001],
            physicsClientId=self.client,
        )

        self._parts.clear()
        self._foreign.clear()
        self._rng = np.random.default_rng(self.seed)
        self._yaw_delta = float(self._rng.uniform(-3, 3)) if self.randomize else 0.0
        self._pitch_delta = float(self._rng.uniform(-2, 2)) if self.randomize else 0.0
        self._light = [1.0, 1.0, 3.0]
        if self.randomize:
            self._light[:2] = self._rng.uniform(0.6, 1.4, 2).tolist()

        camera = self.config.camera

        self._view = p.computeViewMatrixFromYawPitchRoll(
            camera.target,
            camera.distance,
            camera.yaw + self._yaw_delta,
            camera.pitch + self._pitch_delta,
            0,
            2,
        )

        self._projection = p.computeProjectionMatrixFOV(
            60,
            camera.width / camera.height,
            0.05,
            3.0,
        )


    def render_to_file(self, path: str) -> str:
        """Capture and save an RGB image from the virtual camera."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(self.render_rgb()).save(output)
        return str(output)

    def render_rgb(self) -> np.ndarray:
        """Capture one RGB frame (height x width x 3, uint8) without writing a file."""
        camera = self.config.camera

        _, _, rgba, _, _ = p.getCameraImage(
            camera.width,
            camera.height,
            self._view,
            self._projection,
            renderer=p.ER_TINY_RENDERER,
            lightDirection=self._light,
            physicsClientId=self.client,
        )

        image = np.asarray(rgba, dtype=np.uint8)
        return image.reshape(camera.height, camera.width, 4)[:, :, :3].copy()

    
    def place_part(self, part: str) -> None:
        """Place a manufacturing component into the simulation."""

        if part in self._parts:
            raise ValueError(f"{part} is already placed")

        known_parts = {item.name for item in self.config.parts}
        if part not in known_parts:
            raise ValueError(f"Unknown part: {part}")

        rgb = self.config.part(part).color_rgb
        body_ids = []

        def add_box(half_extents, position):
            visual = p.createVisualShape(
                p.GEOM_BOX,
                halfExtents=half_extents,
                rgbaColor=[*rgb, 1],
                physicsClientId=self.client,
            )
            collision = p.createCollisionShape(
                p.GEOM_BOX,
                halfExtents=half_extents,
                physicsClientId=self.client,
            )
            return p.createMultiBody(
                baseMass=0,
                baseCollisionShapeIndex=collision,
                baseVisualShapeIndex=visual,
                basePosition=position,
                physicsClientId=self.client,
            )

        if part == "base_plate":
            body_ids.append(
                add_box([0.18, 0.18, 0.01], [0, 0, 0.01])
            )

        elif part == "gasket":
            body_ids.append(
                add_box([0.16, 0.16, 0.003], [0, 0, 0.023])
            )

        elif part in ("cover", "wrong_cover"):
            body_ids.append(
                add_box([0.12, 0.12, 0.015], [0, 0, 0.041])
            )

        elif part == "bolts":
            positions = [
                (0.10, 0.10),
                (-0.10, 0.10),
                (0.10, -0.10),
                (-0.10, -0.10),
            ]

            for x, y in positions:
                visual = p.createVisualShape(
                    p.GEOM_CYLINDER,
                    radius=0.012,
                    length=0.03,
                    rgbaColor=[*rgb, 1],
                    physicsClientId=self.client,
                )
                collision = p.createCollisionShape(
                    p.GEOM_CYLINDER,
                    radius=0.012,
                    height=0.03,
                    physicsClientId=self.client,
                )
                body = p.createMultiBody(
                    baseMass=0,
                    baseCollisionShapeIndex=collision,
                    baseVisualShapeIndex=visual,
                    basePosition=[x * 1.35, y * 1.35, 0.059],
                    physicsClientId=self.client,
                )
                body_ids.append(body)

        self._parts[part] = body_ids
        if self.randomize:
            dx, dy = self._rng.uniform(-0.003, 0.003, 2)
            self.offset_part(part, float(dx), float(dy))


    
    def remove_part(self, part: str) -> None:
        """Remove a manufacturing component from the simulation."""

        if part not in self._parts:
            raise ValueError(f"{part} is not currently placed")

        body_ids = self._parts[part]

        for body_id in body_ids:
            p.removeBody(
                body_id,
                physicsClientId=self.client,
            )

        del self._parts[part]


    def parts_present(self) -> list[str]:
        """Return the currently tracked components."""
        return list(self._parts)

    # Foreign objects (FOD): things that are not assembly parts, e.g. a tool left on the
    # cover. Each is (colour RGB, [(half_x, half_y, half_z, dx, dy), ...]) in metres.
    FOREIGN_OBJECTS = {
        "screwdriver": ((0.55, 0.15, 0.70), [(0.045, 0.011, 0.008, 0.0, 0.0),
                                              (0.035, 0.003, 0.003, 0.080, 0.0)]),
        "wrench": ((0.12, 0.12, 0.13), [(0.070, 0.011, 0.004, 0.0, 0.0),
                                         (0.016, 0.020, 0.004, 0.080, 0.0)]),
        "rag": ((0.10, 0.60, 0.65), [(0.050, 0.035, 0.003, 0.0, 0.0)]),
    }

    def place_foreign_object(self, kind: str, *, on_assembly: bool = True) -> None:
        """Leave a non-part object in the scene (simulation ground truth only).

        on_assembly=True lays it on top of the stack; False puts it on the table beside it.
        Position and rotation come from the env's seeded RNG, never from the inspector.
        """
        if kind not in self.FOREIGN_OBJECTS:
            raise ValueError(f"Unknown foreign object: {kind}")
        if kind in self._foreign:
            raise ValueError(f"{kind} is already placed")
        rgb, boxes = self.FOREIGN_OBJECTS[kind]
        if on_assembly:
            cx, cy = (float(v) for v in self._rng.uniform(-0.06, 0.06, 2))
            z = 0.08
        else:
            angle = float(self._rng.uniform(0, 2 * np.pi))
            cx, cy, z = 0.27 * np.cos(angle), 0.27 * np.sin(angle), 0.0
        yaw = float(self._rng.uniform(0, np.pi))
        orientation = p.getQuaternionFromEuler([0, 0, yaw])
        body_ids = []
        for hx, hy, hz, dx, dy in boxes:
            visual = p.createVisualShape(p.GEOM_BOX, halfExtents=[hx, hy, hz],
                                         rgbaColor=[*rgb, 1], physicsClientId=self.client)
            x = cx + dx * np.cos(yaw) - dy * np.sin(yaw)
            y = cy + dx * np.sin(yaw) + dy * np.cos(yaw)
            body_ids.append(p.createMultiBody(baseMass=0, baseVisualShapeIndex=visual,
                                              basePosition=[x, y, z + hz],
                                              baseOrientation=orientation,
                                              physicsClientId=self.client))
        self._foreign[kind] = body_ids

    def remove_foreign_object(self, kind: str) -> None:
        if kind not in self._foreign:
            raise ValueError(f"{kind} is not currently placed")
        for body_id in self._foreign.pop(kind):
            p.removeBody(body_id, physicsClientId=self.client)

    def foreign_present(self) -> list[str]:
        """Ground truth: foreign objects in the scene. Must never reach an inspector."""
        return list(self._foreign)

    def close(self) -> None:
        """Disconnect from the physics engine."""
        if p.isConnected(self.client):
            p.disconnect(physicsClientId=self.client)

    def offset_part(self, part: str, dx: float, dy: float, dz: float = 0.0) -> None:
        """Translate an entire component (alignment faults; dz animates the line gantry)."""
        if part not in self._parts:
            raise ValueError(f"{part} is not currently placed")
        self._shift(self._parts[part], dx, dy, dz)

    def offset_foreign_object(self, kind: str, dx: float, dy: float, dz: float = 0.0) -> None:
        if kind not in self._foreign:
            raise ValueError(f"{kind} is not currently placed")
        self._shift(self._foreign[kind], dx, dy, dz)

    def _shift(self, bodies, dx: float, dy: float, dz: float) -> None:
        if not np.isfinite([dx, dy, dz]).all():
            raise ValueError("Offsets must be finite")
        for body in bodies:
            position, orientation = p.getBasePositionAndOrientation(body, physicsClientId=self.client)
            p.resetBasePositionAndOrientation(body, [position[0]+dx, position[1]+dy, position[2]+dz],
                                             orientation, physicsClientId=self.client)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
