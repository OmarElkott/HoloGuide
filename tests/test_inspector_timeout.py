import time

import pytest

from config import load_config
from control.workflow import Workflow, WorkflowState
from vision.inspector import (MockInspector, SlowInspector, TimeoutInspector, is_failure,
                              is_timeout)
from vision.schemas import DecisionStatus


@pytest.fixture
def cfg():
    return load_config()


def test_fast_inspector_passes_through(cfg):
    insp = TimeoutInspector(MockInspector(cfg), timeout_s=1.0)
    obs = insp.inspect("x.png", 1)
    assert not is_failure(obs) and insp.last_status == "ok"


def test_slow_inspector_returns_timeout_result_instead_of_freezing(cfg):
    insp = TimeoutInspector(SlowInspector(MockInspector(cfg), delay_s=1.0), timeout_s=0.1)
    t0 = time.perf_counter()
    obs = insp.inspect("x.png", 1)
    assert time.perf_counter() - t0 < 0.6
    assert is_timeout(obs) and obs.confidence == 0.0 and insp.last_status == "timeout"


def test_exception_inside_inner_is_contained(cfg):
    class Boom(MockInspector):
        def inspect(self, image_path, step):
            raise RuntimeError("kaboom")
    insp = TimeoutInspector(Boom(cfg), timeout_s=1.0)
    obs = insp.inspect("x.png", 1)
    assert is_failure(obs) and insp.last_status == "error"


def test_late_result_is_discarded(cfg):
    insp = TimeoutInspector(SlowInspector(MockInspector(cfg), delay_s=0.3), timeout_s=0.05)
    first = insp.inspect("x.png", 1)
    time.sleep(0.4)                                  # the abandoned call finishes now
    assert is_timeout(first)
    assert insp.inspect("x.png", 1).confidence == 0.0  # next call is independent


def test_workflow_goes_to_hold_on_timeout_and_does_not_advance(cfg):
    insp = TimeoutInspector(SlowInspector(MockInspector(cfg), delay_s=1.0), timeout_s=0.1)
    wf = Workflow(cfg, insp)
    assert wf.check_action("base_plate")
    decision = wf.request_inspection("x.png")
    assert decision.status is DecisionStatus.HOLD
    assert wf.state is WorkflowState.HOLD and wf.current_step_id == 1


def test_mock_modes_drive_the_rules_as_documented(cfg):
    for mode, expected in [("correct", DecisionStatus.PASS), ("missing", DecisionStatus.BLOCK),
                           ("wrong", DecisionStatus.BLOCK), ("uncertain", DecisionStatus.NEEDS_REVIEW)]:
        wf = Workflow(cfg, MockInspector(cfg, mode=mode))
        assert wf.check_action("base_plate")
        assert wf.request_inspection("x.png").status is expected, mode
