#!/usr/bin/env python3
"""Poker module setup - interactive wizard.

Matches the style of the base's `elifelse init` wizard. There is no API key
to enter, so this only asks which decisions you want to make yourself and
prints the config to paste.
"""

import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent
BASE_ROOT = MODULE_ROOT.parent.parent.parent  # data/modules/poker -> base root
BASE_CONFIG = BASE_ROOT / "config.yaml"

AGENT = "agent"
DIFFICULTIES = ["easy", "medium", "hard"]
GAME_MODES = ["tournament", "cash"]


# ~~~ IO helpers (mirrors base WizardIO) ~~~

def say(msg: str) -> None:
    print(f"[system] {msg}")


def yesno(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} ({hint}): ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        say("please answer y or n")


def integer(prompt: str, default: int, minimum: int = 0) -> int:
    while True:
        answer = input(f"{prompt} ({default}): ").strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) >= minimum:
            return int(answer)
        say(f"please enter a whole number >= {minimum}")


def choice(prompt: str, options: list[str], default: str) -> str:
    say(prompt)
    for i, option in enumerate(options, 1):
        say(f"  {i}. {option}")
    default_num = options.index(default) + 1
    while True:
        answer = input(f"choose 1-{len(options)} ({default_num}): ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        say(f"please enter a number from 1 to {len(options)}")


# ~~~ main wizard ~~~

def main():
    settings: dict[str, object] = {}

    say("By default the agent picks the table itself: how many players, which")
    say("bots, tournament or cash game. Answer no to pin any of that yourself.")
    if not yesno("Let the agent set up its own games?", default=True):
        settings["players"] = integer("Players at the table (2-6)", default=4, minimum=2)
        settings["difficulty"] = choice("Bot difficulty:", DIFFICULTIES, default="medium")
        settings["game_mode"] = choice("Game mode:", GAME_MODES, default="tournament")
        if settings["game_mode"] == "cash":
            settings["hands"] = integer("Hands per game (10-100)", default=50, minimum=10)

    say("")
    say("A short game uses faster blinds and 10-25 hands instead of 10-100.")
    short = choice(
        "Short games:", ["agent decides", "yes", "no"], default="agent decides"
    )
    if short != "agent decides":
        settings["short_games"] = short == "yes"

    say("")
    limit = integer("Games per day (0 for no cap)", default=0)
    if limit:
        settings["daily_limit"] = limit

    say("")
    if settings:
        say("add this to your config.yaml:")
        say("  activities:")
        say("    poker:")
        for key, value in settings.items():
            printed = str(value).lower() if isinstance(value, bool) else value
            say(f"      {key}: {printed}")
        say("")
    elif not BASE_CONFIG.exists():
        say(f"no config.yaml at {BASE_CONFIG}, so this is running outside a base install")
        say("")

    say("Done. Start the agent with:  elifelse run")
    say("'Poker' will appear under 'Play a Board or Card Game'.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(1)
