"""Printing the table to the terminal.

A game is hundreds of decisions the framework makes no noise about, so the
module prints its own. The shape follows the rest of the family: a ruled
header per hand, indented lines for what happened, and the agent's own voice
left unindented so it stands out from the table talk.
"""

from __future__ import annotations

import sys

RULE_WIDTH = 60


def _write(line: str) -> None:
    """Print ASCII-safe, the way the base's print_system does."""
    try:
        print(line)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(line.encode(encoding, errors="replace").decode(encoding))


def print_header(text: str) -> None:
    _write("\n" + "=" * RULE_WIDTH + f"\n  {text}\n" + "=" * RULE_WIDTH)


def print_table(text: str) -> None:
    """The table talking: one indented line per event.

    A `--- Hand #3 ... ---` marker in the event log becomes a real header, so
    hands are findable when scrolling back through a long session.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("---") and stripped.endswith("---"):
            print_header(stripped.strip("-").strip())
        else:
            _write(f"  {stripped}")


def print_thinking(text: str) -> None:
    """The agent's reasoning, matching how the base prints it at a menu."""
    if text:
        _write(f"\nThinking: {text}")


def print_action(text: str) -> None:
    if text:
        _write(f"  {text}")
