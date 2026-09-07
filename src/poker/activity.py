"""Poker Activity - the Eli Felse module interface.

Registers as a drop-in activity. Provides:
- A menu entry under the shared "Play a Board or Card Game" line
- A setup menu for table size, bot difficulty, and game mode
- A hand-by-hand betting loop against rule-based bots
- A save you can resume later, and a live table in the base's dashboard

Every setting in the setup menu can be fixed in config.yaml instead, which
removes that question. Left alone, the agent decides all of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .display import print_action, print_header, print_table, print_thinking
from .engine import (
    BOT_DIFFICULTIES,
    DEFAULT_BIG_BLIND,
    DEFAULT_CASH_HANDS,
    DEFAULT_CHIPS,
    DEFAULT_SMALL_BLIND,
    GAME_MODES,
    PokerGame,
    format_chips,
)
from .prompts import action_labels, build_turn_prompt, table_guidance

if TYPE_CHECKING:
    from elifelse.activities.ctx import ActivityContext
    from elifelse.persona import Persona

try:
    from elifelse.activities.base import Activity
except ImportError:
    # Standalone mode - provide a stub
    class Activity:
        key = ""
        menu_label = ""
        menu_group = ""
        requires = []
        requires_base = ""
        schemas = {}
        isolate_context = False
        memory_mode = "standard"
        memory_rules = ""
        survey = None
        dashboard_view = ""
        module_dir = None

        def get_menu_label(self, ctx): return self.menu_label
        def get_status(self, ctx): return ""
        def get_prompt(self, persona): return ""
        def dashboard_state(self, ctx): return None
        def available(self, ctx): return True
        async def startup(self, ctx): pass
        async def run(self, ctx): pass

try:
    from elifelse.textutils import print_system
except ImportError:
    def print_system(text: str) -> None:
        print(f"[system] {text}")


MODULE_ROOT = Path(__file__).resolve().parent.parent.parent

# Config defaults (activities.poker in config.yaml). "agent" means the agent is
# asked during setup; anything else fixes the answer and drops the question.
AGENT = "agent"
DEFAULT_DAILY_LIMIT = 0          # games per day, 0 = no cap
MIN_PLAYERS = 2
MAX_PLAYERS = 6
DEFAULT_PLAYERS = 4
MIN_HANDS = 10
MAX_CASH_HANDS = 100
SHORT_DEFAULT_HANDS = 20
SHORT_MAX_HANDS = 25
NARRATIVE_KEEP = 5


# ~~~ schemas ~~~
def build_setup_schema(fields: list[str]) -> dict[str, Any]:
    """One call for the whole table setup, minus anything config already fixed."""
    properties: dict[str, Any] = {"thinking": {"type": "string"}}
    if "players" in fields:
        properties["num_players"] = {"type": "integer"}
    if "difficulty" in fields:
        properties["difficulty"] = {
            "type": "string",
            "enum": [chr(65 + i) for i in range(len(BOT_DIFFICULTIES))],
        }
    if "game_mode" in fields:
        properties["game_mode"] = {
            "type": "string",
            "enum": [chr(65 + i) for i in range(len(GAME_MODES))],
        }
    if "short_games" in fields:
        properties["session_length"] = {"type": "string", "enum": ["A", "B"]}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


HANDS_SCHEMA = {
    "type": "object",
    "properties": {"thinking": {"type": "string"}, "num_hands": {"type": "integer"}},
    "required": ["thinking", "num_hands"],
    "additionalProperties": False,
}


def build_action_schema(actions: list[str], has_raise: bool) -> dict[str, Any]:
    """The per-turn action menu, lettered like every other menu."""
    properties: dict[str, Any] = {
        "thinking": {"type": "string"},
        "choice": {
            "type": "string",
            "enum": [chr(65 + i) for i in range(len(actions))],
        },
        "return_to_menu": {"type": "boolean"},
    }
    if has_raise:
        # A string, not an integer: models emit a bare number as text far more
        # reliably than they do as a typed JSON field.
        properties["raise_amount"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


class PokerActivity(Activity):
    key = "poker"
    menu_label = "Poker"
    menu_group = "Play a Board or Card Game"
    requires = []
    requires_base = ">=0.5,<1"
    isolate_context = True
    memory_mode = "game_batch"
    survey = "simple"
    dashboard_view = "dashboard/index.html"
    memory_rules = (
        "Extract how the session went as poker, not as a list of hands: the stakes "
        "and format, hands that actually mattered, bluffs called or folded to, how "
        "the agent read the other players, and how it felt about winning or losing "
        "chips. Skip routine folds and the blow-by-blow of the betting."
    )

    def __init__(self) -> None:
        self._game: PokerGame | None = None

    # ~~~ menu presence ~~~
    def get_status(self, ctx: ActivityContext) -> str:
        if self._save_path(ctx).exists():
            return "saved game waiting"
        return super().get_status(ctx)

    def available(self, ctx: ActivityContext) -> bool:
        limit = int(ctx.config.get("daily_limit", DEFAULT_DAILY_LIMIT))
        if limit <= 0:
            return True
        return ctx.limits.remaining(self.key, limit) > 0

    def get_prompt(self, persona: Persona) -> str:
        """Empty: run() sets the full prompt once the table is known."""
        return ""

    def dashboard_state(self, ctx: ActivityContext) -> dict[str, Any] | None:
        return self._game.state.to_dict() if self._game else None

    # ~~~ config ~~~
    def _setting(self, ctx: ActivityContext, name: str) -> Any:
        """A fixed value from config.yaml, or None when the agent decides."""
        value = ctx.config.get(name, AGENT)
        if isinstance(value, str) and value.strip().lower() == AGENT:
            return None
        return value

    def _short_games(self, ctx: ActivityContext) -> bool | None:
        """True/False when you pinned it, None when the agent decides.

        YAML turns `yes`/`no`/`true`/`false` into real booleans, so the value
        arrives here already typed; anything else is read as a word.
        """
        value = ctx.config.get("short_games", AGENT)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text == AGENT:
            return None
        return text in ("true", "yes", "on", "1", "always")

    def _save_path(self, ctx: ActivityContext) -> Path:
        return ctx.data_dir / "poker_save.json"

    # ~~~ the activity ~~~
    async def run(self, ctx: ActivityContext) -> str:
        save_path = self._save_path(ctx)
        resumed = False

        if save_path.exists():
            saved = self._read_save(save_path)
            if saved:
                choice = await ctx.choose(
                    f"You have a saved poker game: {self._describe_save(saved)}",
                    options=["resume", "new"],
                    labels=["Resume saved game", "Start a new game (deletes the save)"],
                )
                if choice == "resume":
                    self._game = PokerGame.restore(saved, pace=self._pace(ctx))
                    resumed = True
                else:
                    save_path.unlink()

        if self._game is None:
            self._game = await self._new_game(ctx)
            if self._game is None:
                return "Could not set up a poker table."

        game = self._game
        try:
            return await self._play(ctx, game, resumed)
        finally:
            self._game = None

    async def _new_game(self, ctx: ActivityContext) -> PokerGame | None:
        """Ask for whatever config left open, then seat the table."""
        fixed_mode = self._setting(ctx, "game_mode")
        fixed_difficulty = self._setting(ctx, "difficulty")
        fixed_players = self._setting(ctx, "players")
        fixed_hands = self._setting(ctx, "hands")
        fixed_short = self._short_games(ctx)

        mode_keys = list(GAME_MODES)
        difficulty_keys = list(BOT_DIFFICULTIES)

        fields = []
        if fixed_players is None:
            fields.append("players")
        if fixed_difficulty is None:
            fields.append("difficulty")
        if fixed_mode is None:
            fields.append("game_mode")
        if fixed_short is None:
            fields.append("short_games")

        short_game = bool(fixed_short)
        players = fixed_players
        difficulty = fixed_difficulty
        game_mode = fixed_mode

        if fields:
            answer = await ctx.generate(
                self._setup_prompt(fields, short_game), build_setup_schema(fields)
            )
            if "players" in fields:
                players = answer.get("num_players", DEFAULT_PLAYERS)
            if "difficulty" in fields:
                difficulty = self._pick(answer.get("difficulty"), difficulty_keys)
            if "game_mode" in fields:
                game_mode = self._pick(answer.get("game_mode"), mode_keys)
            if "short_games" in fields:
                short_game = answer.get("session_length") == "B"

        players = self._clamp_int(players, MIN_PLAYERS, MAX_PLAYERS, DEFAULT_PLAYERS)
        difficulty = difficulty if difficulty in BOT_DIFFICULTIES else "medium"
        game_mode = game_mode if game_mode in GAME_MODES else "tournament"

        num_hands = DEFAULT_CASH_HANDS
        if GAME_MODES[game_mode]["fixed_hands"]:
            default_hands = SHORT_DEFAULT_HANDS if short_game else DEFAULT_CASH_HANDS
            top = SHORT_MAX_HANDS if short_game else MAX_CASH_HANDS
            if fixed_hands is None:
                answer = await ctx.generate(
                    "How many hands would you like to play? "
                    f"(choose {MIN_HANDS}-{top}, default {default_hands})",
                    HANDS_SCHEMA,
                )
                num_hands = answer.get("num_hands", default_hands)
            else:
                num_hands = fixed_hands
            num_hands = self._clamp_int(num_hands, MIN_HANDS, top, default_hands)

        game = PokerGame(
            players, difficulty, game_mode, num_hands, short_game=short_game,
            agent_name=ctx.persona.name,
            starting_chips=int(ctx.config.get("starting_chips", DEFAULT_CHIPS)),
            small_blind=int(ctx.config.get("small_blind", DEFAULT_SMALL_BLIND)),
            big_blind=int(ctx.config.get("big_blind", DEFAULT_BIG_BLIND)),
            pace=self._pace(ctx),
        )
        game.start()
        return game

    def _setup_prompt(self, fields: list[str], short_game: bool) -> str:
        lines = ["Set up your poker game (Texas Hold'em)."]
        if "players" in fields:
            lines += [
                "",
                f"Number of Players: choose {MIN_PLAYERS}-{MAX_PLAYERS} "
                f"(includes you, default {DEFAULT_PLAYERS})",
            ]
        if "difficulty" in fields:
            lines += ["", "Difficulty:"]
            lines += [
                f"  {chr(65 + i)}. {value['menu']}"
                for i, value in enumerate(BOT_DIFFICULTIES.values())
            ]
        if "game_mode" in fields:
            lines += ["", "Game Mode:"]
            lines += [
                f"  {chr(65 + i)}. {value['short_menu'] if short_game else value['menu']}"
                for i, value in enumerate(GAME_MODES.values())
            ]
        if "short_games" in fields:
            lines += [
                "",
                "Session Length:",
                "  A. Full session",
                f"  B. Short session (faster blinds, {MIN_HANDS}-{SHORT_MAX_HANDS} hands)",
            ]
        return "\n".join(lines)

    async def _play(self, ctx: ActivityContext, game: PokerGame, resumed: bool) -> str:
        save_path = self._save_path(ctx)
        ctx.app.provider.set_system_prompt("\n\n".join([
            ctx.app.base_prompt(),
            table_guidance(game._game_mode, game._difficulty["name"], game._short_game),
        ]))
        ctx.set_status("Playing poker", {"game": game._mode["name"]})
        print_header(f"POKER{' - RESUMED' if resumed else ''}")
        print_table(game.table_line())

        turns = 0
        while not game.is_game_over():
            events = await game.advance_to_agent()
            print_table(events)
            turn = game.get_turn_state()
            if turn is None:
                break

            labels = action_labels(
                turn["actions"], turn["call_amount"], turn["min_raise"], turn["max_raise"]
            )
            prompt = build_turn_prompt(turn)
            if events:
                prompt = f"{events}\n\n{prompt}"
            prompt += "\n\nYour action options:\n" + "\n".join(
                f"{chr(65 + i)}) {label}" for i, label in enumerate(labels)
            )

            has_raise = "raise" in turn["actions"]
            print_table(
                f"Pot {format_chips(turn['pot'])} | your chips "
                f"{format_chips(turn['player'].chips)} | "
                + " ".join(f"{chr(65 + i)}) {label}" for i, label in enumerate(labels))
            )
            answer = await ctx.generate(prompt, build_action_schema(turn["actions"], has_raise))
            turns += 1
            print_thinking(answer.get("thinking", ""))

            ctx.remember_game("user", prompt[:500])
            ctx.remember_game(
                "assistant",
                f"{answer.get('thinking', '')}\nChoice: {answer.get('choice', '')}",
            )

            if answer.get("return_to_menu"):
                left = await self._quit_menu(ctx, game, save_path)
                if left:
                    return left

            print_action(game.apply_choice(
                answer.get("choice", ""),
                thinking=answer.get("thinking", ""),
                raise_amount=self._raise_amount(answer.get("raise_amount")),
            ))

        result = game.get_result()
        if save_path.exists():
            save_path.unlink()
        self._record(ctx, game, result)
        print_header(f"POKER RESULT: {result['result_text']}")
        ctx.remember_game("user", f"[Game result] {result['summary']}")
        return f"Played {turns} poker hands. {result['result_text']}"

    async def _quit_menu(self, ctx: ActivityContext, game: PokerGame, save_path: Path) -> str:
        """A mid-game exit. Returns the closing note, or '' to keep playing."""
        choice = await ctx.choose(
            "You're in the middle of a poker game.",
            options=["quit", "save", "cancel"],
            labels=[
                "Quit game (counts as a loss)",
                "Save game and quit (you can resume later)",
                "Cancel, keep playing",
            ],
        )
        if choice == "cancel":
            return ""
        if choice == "save":
            save_path.write_text(
                json.dumps(game.save_data(), indent=2), encoding="utf-8"
            )
            ctx.app.stats.increment("poker.games_saved")
            return f"Saved the poker game after {game.state.hand_number} hands."

        game.record_loss()
        result = game.get_result()
        if save_path.exists():
            save_path.unlink()
        self._record(ctx, game, result)
        return result["result_text"]

    # ~~~ bookkeeping ~~~
    def _record(self, ctx: ActivityContext, game: PokerGame, result: dict[str, Any]) -> None:
        stats = ctx.app.stats
        stats.increment("poker.games_played")
        stats.increment("poker.hands_played", game.state.hand_number)
        stats.increment({"W": "poker.wins", "L": "poker.losses"}.get(result["result"], "poker.draws"))
        limit = int(ctx.config.get("daily_limit", DEFAULT_DAILY_LIMIT))
        if limit > 0:
            ctx.limits.use(self.key)

    # ~~~ small helpers ~~~
    def _pace(self, ctx: ActivityContext) -> bool:
        """Pausing between bot actions is for watching, so tests turn it off."""
        return bool(ctx.config.get("pace_table", True))

    @staticmethod
    def _pick(letter: Any, keys: list[str]) -> str:
        index = ord(str(letter).upper()) - 65 if letter else -1
        return keys[index] if 0 <= index < len(keys) else keys[0]

    @staticmethod
    def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
        try:
            return max(low, min(int(value), high))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _raise_amount(value: Any) -> int:
        try:
            return int(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _read_save(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print_system(f"could not read the poker save: {e}")
            return None

    @staticmethod
    def _describe_save(saved: dict[str, Any]) -> str:
        mode = GAME_MODES.get(saved.get("game_mode", ""), {}).get("name", "game")
        agent = next((p for p in saved.get("players", []) if p.get("is_agent")), {})
        chips = format_chips(agent.get("chips", 0))
        return f"{mode}, {saved.get('hand_number', 0)} hands in, {chips} chips"
