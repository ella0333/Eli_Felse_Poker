"""Fixtures, mirroring the base's own tests/conftest.py.

The module is a drop-in folder rather than an installed package, so the repo
root goes on sys.path the same way standalone.py reaches it.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from elifelse.app import App  # noqa: E402
from elifelse.config import Config  # noqa: E402
from elifelse.persona import Persona  # noqa: E402
from elifelse.providers.mock import MockProvider  # noqa: E402

from src.poker.activity import PokerActivity  # noqa: E402


@pytest.fixture
def config(tmp_path):
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.provider.kind = "mock"
    cfg.provider.response_delay_min = 0
    cfg.provider.response_delay_max = 0
    cfg.memory.enabled = False
    cfg.day_cycle.enabled = False
    # No pauses between bot actions: they are there for watching a real table.
    cfg.activities["poker"] = {"pace_table": False}
    return cfg


@pytest.fixture
def mock_provider(config):
    return MockProvider(config)


@pytest.fixture
def persona():
    return Persona(name="Testa", pronouns="they/them", personality="A test persona.")


@pytest.fixture
def app(config, persona, mock_provider):
    """A full App on a temp data dir with a MockProvider. No activities
    discovered - tests register exactly what they need."""
    return App(config, persona, provider=mock_provider)


@pytest.fixture
def poker(app):
    app.registry.register(PokerActivity)
    activity = app.registry.get("poker")
    return activity, app.registry.ctx_for(activity)
