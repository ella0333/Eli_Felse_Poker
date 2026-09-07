"""Eli Felse Poker - drop-in module entry point.

When installed at data/modules/poker/, the base framework auto-discovers this
file and reads the ACTIVITIES list to register the activity.
"""

try:
    from .src.poker.activity import PokerActivity
except ImportError:
    # Loaded as a plain top-level module rather than as a package, which is
    # what happens when the repo is used standalone or under pytest.
    from src.poker.activity import PokerActivity

ACTIVITIES = [PokerActivity]
