# Eli Felse | Poker

[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)

!["Eli" Chibi Character with grey hair and green eyes, and grey x hair clip holding playing cards](https://i.imgur.com/nieJtMP.png)

**A module for [Eli Felse Base](https://github.com/ella0333/Eli_Felse_Base) that lets
an AI agent play no-limit Texas Hold'em against bot players in sit-and-go tournaments
or cash games.**

The agent configures the table by default: how many players, which bots, and which playing
format. You can manually pin any of the options in `config.yaml`.

## Quick start

```bash
cd Eli_Felse_Base
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
git clone https://github.com/ella0333/Eli_Felse_Poker.git data/modules/poker
cd data/modules/poker
pip install -r requirements.txt
python scripts/setup.py       # optional, game configuration
cd ../../..                   # Back to Eli_Felse_Base
elifelse run
```

"Poker" will appear under "Play a Board or Card Game" in the activity menu,
alongside any other card game you have installed.

New to all of this? Follow the **[base project's guide](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/getting-started.md)** and come back here when you are finished

Need help choosing a compatible model? See the 
**[Model Options Guide](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/model-options.md)**

Requires base **0.5.0 or newer**.

## Viewing the gameplay table 

Type `/dashboard` while the agent is running and open
**http://127.0.0.1:8080**

## Settings

Every setting takes `agent`, the default, which means the agent is asked during setup.
Add the value yourself instead, and the agent is no longer prompted to decide.

```yaml
activities:
  poker:
    game_mode: agent       # agent | tournament | cash
    short_games: agent     # agent | true | false
    difficulty: agent      # agent | easy | medium | hard
    players: agent         # agent | 2 to 6
    hands: agent           # agent | 10 to 100   (cash games only)
```

`short_games` makes a game fit in one sitting: faster blinds, and 10 to 25 hands
instead of 10 to 100. Chip and blind sizes, a daily cap, and how to add a new game
mode are all in **[module integration](docs/module-integration.md)**.

## Standalone poker harness

You can also run this module without the Eli Felse framework. Connect any
**[compatible model](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/model-options.md)** and let it play directly.

```bash
git clone https://github.com/ella0333/Eli_Felse_Poker.git
cd Eli_Felse_Poker
python -m venv .venv
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python standalone_setup.py    # configures the model connection
python standalone.py          # play
```

The model sets up a table and plays it out, one game after another, until it spends
the session's game limit (3 by default, set during setup). The same table is served at
**http://127.0.0.1:8080** while it plays. Press `Ctrl+C` at any time to quit.

## Docs

- **[Module integration](docs/module-integration.md):** how it plugs into the base, menus, config, memory
- **[Standalone mode](docs/standalone-mode.md):** standalone harness setup and how sessions work

## Learn More about the Project

Meet Eli Felse, a framework built to explore safer ways to create autonomous AI assistants.
Eli Felse is part of a public demo, weekly blogs, open-source releases, and a growing community.

**[24/7 live public demo](https://elifelse.org/eli/)**

**[Introduction Blog](https://elifelse.org/dev-blog/meet-eli)**

**[Join the Discord community](https://discord.com/invite/2C4znNnyM7)**

---

[![elif else](https://img.shields.io/badge/elif_else-869b9f?style=flat)](https://elifelse.org/)
[![MIT License](https://img.shields.io/badge/MIT_License-869b9f?style=flat)](LICENSE)
