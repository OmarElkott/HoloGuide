
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

    def render_to_file(self, path: str) -> str:
        """Capture and save an RGB image from the virtual camera."""
        camera = self.config.camera

        view = p.computeViewMatrixFromYawPitchRoll(
            camera.target,
            camera.distance,
            camera.yaw,
            camera.pitch,
            0,
            2,
        )

        projection = p.computeProjectionMatrixFOV(
            60,
            camera.width / camera.height,
            0.05,
            3.0,
        )

        _, _, rgba, _, _ = p.getCameraImage(
            camera.width,
            camera.height,
            view,
            projection,
            renderer=p.ER_TINY_RENDERER,
            physicsClientId=self.client,
        )

        image = np.asarray(rgba, dtype=np.uint8)
        image = image.reshape(
            camera.height, camera.width, 4
        )[:, :, :3]

        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(output)

        return str(output)

    def place_part(self, part: str) -> None:
        """Add a part (to be implemented in SIM-02)."""
        raise NotImplementedError("Coming in SIM-02")

    def remove_part(self, part: str) -> None:
        """Remove a part (to be implemented in SIM-02)."""
        raise NotImplementedError("Coming in SIM-02")

    def parts_present(self) -> list[str]:
        """Return the currently tracked components."""
        return list(self._parts)

    def close(self) -> None:
        """Disconnect from the physics engine."""
        if p.isConnected(self.client):
            p.disconnect(physicsClientId=self.client)
