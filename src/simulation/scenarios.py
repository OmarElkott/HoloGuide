"""Immutable scenario descriptions; labels never enter the inspection API."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Action:
    kind: Literal['place', 'remove']
    part: str


@dataclass(frozen=True)
class Scenario:
    name: str
    actions: list[Action]
    expected_first_status: str


NAMES = ('correct', 'missing_gasket', 'wrong_cover', 'wrong_order')


def get_scenario(name: str) -> Scenario:
    """Return fresh actions describing the initial scene, before correction."""
    scenes = {
        'correct': ('base_plate', 'gasket', 'cover', 'bolts'),
        'missing_gasket': ('base_plate', 'cover'),
        'wrong_cover': ('base_plate', 'gasket', 'wrong_cover'),
        'wrong_order': ('cover',),
    }
    if name not in scenes:
        raise ValueError(f'Unknown scenario: {name}')
    return Scenario(name, [Action('place', p) for p in scenes[name]],
                    'pass' if name == 'correct' else 'block')


def apply_actions(environment, actions):
    """Scene construction only; does not authorize workflow advancement."""
    for action in actions:
        if action.kind not in ('place', 'remove'):
            raise ValueError(f'Unknown action: {action.kind}')
        getattr(environment, action.kind + '_part')(action.part)
