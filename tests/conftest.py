"""Shared Person 4 fixtures; no model, GPU, or simulator required."""
import pytest
from src.config import load_config
from src.control.cli import MockInspector


@pytest.fixture
def assembly_config():
    return load_config()


@pytest.fixture
def mock_inspector(assembly_config):
    return MockInspector(assembly_config)
