# Module integration

How the Poker module plugs into [Eli Felse Base](https://github.com/ella0333/Eli_Felse_Base).
If you have not set the base up yet, start with its
[getting started guide](https://github.com/ella0333/Eli_Felse_Base/blob/master/docs/getting-started.md).

## Installation

```bash
cd Eli_Felse_Base
.venv\Scripts\activate
git clone https://github.com/ella0333/Eli_Felse_Poker.git data/modules/poker
cd data/modules/poker
pip install -r requirements.txt
```

The base scans `data/modules/` at startup and reads the `ACTIVITIES` list out of the
folder's `__init__.py`. There is nothing to register.

This module needs base **0.5.0 or newer**, which is the release that added shared menu
lines and module dashboard pages.

## Menu structure

Poker does not take a main-menu line of its own. It declares
`menu_group = "Play a Board or Card Game"`, so the base folds it into a shared line
with any other module that declares the same group:

```
D) Play a Board or Card Game (Poker, Blackjack)
```

Picking that opens the group's sub-menu, and picking the game runs it:

```
Play a Board or Card Game

A) Poker (last: 2 hours ago)
B) Blackjack
```

Install Poker on its own and the line reads `Play a Board or Card Game (Poker)`.
The anti-repeat rule still applies per game, so playing poker holds poker
back for a turn without hiding blackjack.

### Sub-menus

1. **Resume**, only when a save is waiting: resume, or start fresh and delete the save.
2. **Setup**, asking only for what config left open: table size, bot difficulty, game
   mode, and session length.
3. **Number of hands**, cash games only.
4. **Quit menu**, when the agent sets `return_to_menu` mid-game: quit and take the
   loss, save and quit, or cancel and keep playing.

## Config

Add an optional section to your `config.yaml`. Every key is optional, and the module
runs with none of them.

```yaml
activities:
  poker:
    game_mode: agent       # agent | tournament | cash
    short_games: agent     # agent | true | false
    difficulty: agent      # agent | easy | medium | hard
    players: agent         # agent | 2 to 6
    hands: agent           # agent | 10 to 100   (cash games only)
    starting_chips: 1000
    small_blind: 10
    big_blind: 20
    daily_limit: 0         # games per day, 0 = no cap
    pace_table: true       # pause between bot actions so the table is watchable
```

`agent` means the agent is asked during setup. Any other value fixes the answer and
drops that question from the setup menu, so pinning all of them means the agent is
never asked anything and sits straight down.

There is no API key, so nothing goes in the base's `.env` and `requires` is empty.

## The bots

Bot names come from a shared pool and all share one difficulty. Easy bots play loose
and passive, medium bots read pot odds, and hard bots read position too and bluff
about one hand in seven. Every difficulty makes deliberate mistakes at its own rate,
so none of them play a solved game.

## Adding a game mode

`GAME_MODES` in `src/poker/engine.py` is the list. An entry declares its menu wording
and four rules:

```python
"cash": {
    "name": "Cash Game",
    "menu": "Cash Game (play a set number of hands, track profit/loss)",
    "short_menu": "Cash Game (play a set number of hands, track profit/loss)",
    "eliminates": False,     # does busting put a player out?
    "rebuys": True,          # does busting buy back in?
    "rising_blinds": False,  # do the blinds climb?
    "fixed_hands": True,     # does it run for a set number of hands?
},
```

Add a key and it becomes a valid `game_mode` in the config and a line in the setup
menu, in that order, with nothing else to change. Bot difficulties work the same way
through `BOT_DIFFICULTIES`.

## The dashboard page

`dashboard_view = "dashboard/index.html"` gives the module a tab in the base's
dashboard. Start it with the `/dashboard` command and open
**http://127.0.0.1:8080**, the base's own dashboard port. The page polls
`/api/module-state?key=poker` twice a second and draws the felt itself.

The standalone harness serves the same page on the same port, since it is what you
run instead of the base rather than alongside it.

The base serves only the `dashboard/` folder, so the rest of the module is not
reachable over HTTP. The payload hides every other player's hole cards until a
showdown reveals them, which is the same rule the agent plays under.

## Data storage

One file, `data/activities/poker/poker_save.json`, and only while a game is saved.
It holds chips, seats, hand number and settings, so a resume keeps the table and
deals a fresh hand. Everything else goes through the base's own stores.

## Context isolation

`isolate_context = True` and `memory_mode = "game_batch"`. A poker session is hundreds
of betting decisions, so the framework snapshots context before the game and restores
it after, and memories are batched three messages at a time without the classifier.
What survives is the summary, not the blow-by-blow.

## Stats

`poker.games_played`, `poker.hands_played`, `poker.wins`, `poker.losses`,
`poker.draws`, and `poker.games_saved`, all visible on the dashboard's status page.

## Schemas

The setup menu, the hand count, and each betting decision are all schema-constrained
JSON: a letter from an enum, plus an integer or a digit string. `raise_amount` is a
string rather than an integer because models emit a bare number as text far more
reliably, and it is parsed and clamped to the legal range on the Python side before
a single chip moves.

An unusable letter never costs chips. It checks if checking is free, and folds if it
is not, rather than guessing at a bet.
