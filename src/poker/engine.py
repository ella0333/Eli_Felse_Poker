"""Texas Hold'em No-Limit, played by an agent against rule-based bots.

Pure game logic. Nothing here talks to a model, a network, or a file: the
activity drives it by asking what the agent must decide and handing back a
letter. Bots, hand evaluation, side pots and showdown are all decided in
Python, so the only thing the model ever supplies is a choice from a menu.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from treys import Card, Deck, Evaluator

# ~~~ table constants ~~~
DEFAULT_CHIPS = 1000
DEFAULT_SMALL_BLIND = 10
DEFAULT_BIG_BLIND = 20
BLIND_INCREASE_HANDS = 10
SHORT_BLIND_INCREASE_HANDS = 4   # a tournament that has to finish in one sitting
BLIND_INCREASE_FACTOR = 1.5
MAX_HANDS = 200
DEFAULT_CASH_HANDS = 50

BOT_NAMES = ["Alice", "Bob", "Carlos", "Diana", "Eddie"]

CHARS_PER_TOKEN = 4

# How long the table pauses so a watcher can follow it. Tests pass 0.
BOT_ACTION_PAUSE = 0.3
SHOWDOWN_PAUSE = 1.0


# ~~~ game modes ~~~
# Adding a mode here is all it takes: the key becomes a valid `game_mode` in
# config.yaml and a line in the setup menu, in this order.
GAME_MODES = {
    "tournament": {
        "name": "Tournament",
        "menu": "Tournament (play until one player has all chips, blinds increase)",
        "short_menu": "Tournament (play until one player has all chips, blinds increase quickly)",
        "eliminates": True,      # a busted player is out for good
        "rebuys": False,
        "rising_blinds": True,
        "fixed_hands": False,    # runs until one player is left, capped at MAX_HANDS
    },
    "cash": {
        "name": "Cash Game",
        "menu": "Cash Game (play a set number of hands, track profit/loss)",
        "short_menu": "Cash Game (play a set number of hands, track profit/loss)",
        "eliminates": False,
        "rebuys": True,          # busting means buying back in, not going out
        "rising_blinds": False,
        "fixed_hands": True,
    },
}


# ~~~ bot difficulties ~~~
BOT_DIFFICULTIES = {
    "easy": {
        "name": "Easy",
        "menu": "Easy (loose/passive bots)",
        "fold_threshold": 0.25,
        "bluff_chance": 0.02,
        "aggression": 0.15,
        "tightness": 0.40,
        "pot_odds_aware": False,
        "positional_aware": False,
        "mistake_chance": 0.15,
    },
    "medium": {
        "name": "Medium",
        "menu": "Medium (balanced bots)",
        "fold_threshold": 0.40,
        "bluff_chance": 0.08,
        "aggression": 0.35,
        "tightness": 0.55,
        "pot_odds_aware": True,
        "positional_aware": False,
        "mistake_chance": 0.08,
    },
    "hard": {
        "name": "Hard",
        "menu": "Hard (tight/aggressive bots)",
        "fold_threshold": 0.35,
        "bluff_chance": 0.15,
        "aggression": 0.50,
        "tightness": 0.60,
        "pot_odds_aware": True,
        "positional_aware": True,
        "mistake_chance": 0.03,
    },
}


# ~~~ cards ~~~
evaluator = Evaluator()

RANK_WORDS = {
    "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six", "7": "Seven",
    "8": "Eight", "9": "Nine", "T": "Ten", "J": "Jack", "Q": "Queen",
    "K": "King", "A": "Ace",
}
SUIT_WORDS = {"h": "Hearts", "d": "Diamonds", "c": "Clubs", "s": "Spades"}


def card_str(card: int) -> str:
    return Card.int_to_str(card)


def cards_str(cards) -> str:
    return " ".join(card_str(c) for c in cards)


def card_long(card: int) -> str:
    """Spell a card out in full, e.g. 'Queen of Diamonds'.

    The two-letter form alone ('Qd 8c') reads as shorthand and gets misread,
    suits in particular, which is how an offsuit hand once got called suited.
    """
    text = card_str(card)
    return f"{RANK_WORDS.get(text[0], text[0])} of {SUIT_WORDS.get(text[1], text[1])}"


def cards_long(cards) -> str:
    return ", ".join(card_long(c) for c in cards)


def describe_hole_cards(hole_cards) -> str:
    """The two hole cards spelled out, plus pair / suited / offsuit."""
    if len(hole_cards) != 2:
        return cards_long(hole_cards)
    first, second = (card_str(c) for c in hole_cards)
    if first[0] == second[0]:
        shape = f"a POCKET PAIR of {RANK_WORDS.get(first[0], first[0])}s"
    elif first[1] == second[1]:
        shape = f"SUITED, both {SUIT_WORDS.get(first[1], first[1])}"
    else:
        shape = "OFFSUIT, two different suits"
    return f"{cards_long(hole_cards)} - {shape}"


def describe_made_hand(hole_cards, community_cards) -> str:
    """Name the best five-card hand available right now, e.g. 'One Pair'.

    Uses the evaluator the showdown uses, so the hand the agent is told it has
    is the hand that actually gets scored.
    """
    if len(community_cards) < 3 or len(hole_cards) != 2:
        return ""
    try:
        rank = evaluator.evaluate(list(hole_cards), list(community_cards))
        return evaluator.class_to_string(evaluator.get_rank_class(rank))
    except Exception:
        return ""


def estimate_preflop_strength(hole_cards, sims: int = 200) -> float:
    """Monte Carlo preflop strength against one random opponent (0.0-1.0)."""
    wins = 0.0
    for _ in range(sims):
        deck = Deck()
        deck.cards = [c for c in deck.cards if c not in hole_cards]
        random.shuffle(deck.cards)
        board = [deck.cards[i] for i in range(5)]
        opponent = [deck.cards[5], deck.cards[6]]
        mine = evaluator.evaluate(hole_cards, board)
        theirs = evaluator.evaluate(opponent, board)
        if mine < theirs:
            wins += 1
        elif mine == theirs:
            wins += 0.5
    return wins / sims


def evaluate_hand_strength(hole_cards, community_cards) -> float:
    """Hand strength as 0.0-1.0, 1.0 being the nuts."""
    if len(community_cards) < 3:
        return estimate_preflop_strength(hole_cards)
    rank = evaluator.evaluate(hole_cards, community_cards)
    return 1.0 - (rank - 1) / 7461


def format_chips(n: int) -> str:
    return f"{n:,}"


# ~~~ session narrative ~~~
class SessionNarrative:
    """One condensed line per hand, so the prompt can look back at the session."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def add_hand(self, hand_number: int, summary: str) -> None:
        self.entries.append(f"Hand #{hand_number}: {summary}")

    def build_context(self, budget_tokens: int) -> str:
        """The most recent hands that fit the budget, oldest dropped first."""
        if not self.entries:
            return ""
        budget_chars = budget_tokens * CHARS_PER_TOKEN
        selected: list[str] = []
        total = 0
        for entry in reversed(self.entries):
            size = len(entry) + 1
            if total + size > budget_chars:
                break
            selected.append(entry)
            total += size
        selected.reverse()
        return "\n".join(selected)


def build_hand_summary(agent, community_cards, hand_action_log, results, chips_before) -> str:
    """One line for a finished hand: cards, board, what the agent did, result."""
    hole = cards_str(agent.hole_cards) if agent.hole_cards else "??"
    board = cards_str(community_cards) if community_cards else ""

    actions = []
    for entry in hand_action_log:
        if not entry.startswith(f"{agent.name}:"):
            continue
        part = entry[len(agent.name) + 1:].strip()
        if part.startswith("fold"):
            actions.append("folded")
        elif part.startswith("check"):
            actions.append("checked")
        elif part.startswith("call"):
            actions.append(part.replace("call", "called", 1))
        elif part.startswith("raise"):
            actions.append(part.replace("raise", "raised", 1))
        else:
            actions.append(part)
    actions_str = ", ".join(actions) if actions else "no actions"

    # Folding keeps you out of the showdown results, so the chip delta is the
    # only thing that always knows what the hand cost.
    own = next((r for r in results if r["name"] == agent.name), None)
    hand_name = own.get("hand_name", "") if own else ""
    diff = agent.chips - chips_before
    if diff > 0:
        result_str = f"Won {format_chips(diff)}"
        if hand_name and hand_name != "Winner (others folded)":
            result_str += f" ({hand_name})"
    elif diff < 0:
        result_str = f"Lost {format_chips(abs(diff))}"
        if hand_name:
            result_str += f" ({hand_name})"
    else:
        result_str = "Break even"

    if board:
        return f"{hole} | {board} | {actions_str} | {result_str}"
    return f"{hole} | {actions_str} | {result_str}"


# ~~~ players and table state ~~~
class Player:
    def __init__(self, name: str, chips: int, is_agent: bool = False, difficulty=None) -> None:
        self.name = name
        self.chips = chips
        self.is_agent = is_agent
        self.difficulty = difficulty
        self.hole_cards: list[int] = []
        self.current_bet = 0
        self.total_bet = 0
        self.is_folded = False
        self.is_all_in = False
        self.is_eliminated = False
        self.seat = 0
        self.rebuys = 0

    def reset_for_hand(self) -> None:
        self.hole_cards = []
        self.current_bet = 0
        self.total_bet = 0
        self.is_folded = False
        self.is_all_in = False

    def to_dict(self, reveal: bool = False) -> dict[str, Any]:
        show = (reveal or self.is_agent) and self.hole_cards and not self.is_eliminated
        return {
            "name": self.name,
            "chips": self.chips,
            "current_bet": self.current_bet,
            "is_folded": self.is_folded,
            "is_all_in": self.is_all_in,
            "is_eliminated": self.is_eliminated,
            "is_agent": self.is_agent,
            "hole_cards": [card_str(c) for c in self.hole_cards] if show else [],
            "has_cards": bool(self.hole_cards) and not self.is_folded and not self.is_eliminated,
            "seat": self.seat,
        }


class TableState:
    """Everything the dashboard page draws. One per game, no globals."""

    def __init__(self) -> None:
        self.players: list[Player] = []
        self.community_cards: list[int] = []
        self.pot = 0
        self.dealer_index = 0
        self.hand_number = 0
        self.small_blind = DEFAULT_SMALL_BLIND
        self.big_blind = DEFAULT_BIG_BLIND
        self.phase = "waiting"
        self.status = "waiting"
        self.action_log: list[dict[str, Any]] = []
        self.thinking_log: list[dict[str, Any]] = []
        self.current_actor = ""
        self.winner = ""
        self.showdown_results: list[dict[str, Any]] = []
        self.reveal_cards = False
        self.bot_difficulty_name = ""
        self.game_mode = "tournament"
        self.total_hands = 0
        self.starting_chips = DEFAULT_CHIPS
        self.profit_loss: dict[str, int] = {}
        self.agent_name = "the agent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "game": "poker",
            "agent_name": self.agent_name,
            "players": [p.to_dict(reveal=self.reveal_cards) for p in self.players],
            "community_cards": [card_str(c) for c in self.community_cards],
            "pot": self.pot,
            "dealer_index": self.dealer_index,
            "hand_number": self.hand_number,
            "small_blind": self.small_blind,
            "big_blind": self.big_blind,
            "phase": self.phase,
            "status": self.status,
            "action_log": self.action_log[-40:],
            "thinking_log": self.thinking_log[-5:],
            "current_actor": self.current_actor,
            "winner": self.winner,
            "showdown_results": self.showdown_results,
            "reveal_cards": self.reveal_cards,
            "bot_difficulty": self.bot_difficulty_name,
            "game_mode": self.game_mode,
            "total_hands": self.total_hands,
            "profit_loss": dict(self.profit_loss),
        }


# ~~~ rules ~~~
def get_legal_actions(player: Player, current_bet: int, min_raise_to: int, big_blind: int):
    """(actions, call_amount, min_raise_to, max_raise_to) for one player."""
    actions = ["fold"]
    call_amount = current_bet - player.current_bet

    if call_amount <= 0:
        actions.append("check")
        call_amount = 0
    else:
        if player.chips > 0:
            actions.append("call")
        call_amount = min(call_amount, player.chips)

    chips_after_call = player.chips - call_amount
    if chips_after_call > 0:
        actual_min = max(min_raise_to, current_bet + big_blind)
        player_max = player.chips + player.current_bet
        if player_max >= actual_min:
            actions.append("raise")
            return actions, call_amount, min(actual_min, player_max), player_max
        if player.chips > call_amount:
            # Short stacks can still shove for less than a legal raise.
            actions.append("raise")
            return actions, call_amount, player_max, player_max

    return actions, call_amount, min_raise_to, player.chips + player.current_bet


def _bot_correct_action(player, community_cards, pot, current_bet, min_raise_to,
                        max_raise_to, actions_list, call_amount, active_count,
                        dealer_distance, big_blind):
    """The strategically correct bot play, before the mistake roll."""
    diff = player.difficulty
    strength = evaluate_hand_strength(player.hole_cards, community_cards)
    is_preflop = len(community_cards) == 0

    if diff["positional_aware"] and active_count > 2:
        strength = min(1.0, strength + (active_count - 1 - dealer_distance) * 0.03)

    if strength < 0.3 and random.random() < diff["bluff_chance"] and "raise" in actions_list:
        bluff_to = random.choice([
            min_raise_to,
            min(int(pot * 0.5) + current_bet, max_raise_to),
            min(int(pot * 0.75) + current_bet, max_raise_to),
        ])
        return "raise", max(min_raise_to, min(bluff_to, max_raise_to))

    fold_line = diff["tightness"] * (0.6 if is_preflop else 0.5)
    if strength < fold_line:
        if call_amount == 0 and "check" in actions_list:
            return "check", 0
        if "fold" in actions_list:
            return "fold", 0
        return "check", 0

    if diff["pot_odds_aware"] and call_amount > 0:
        pot_odds = call_amount / (pot + call_amount) if (pot + call_amount) > 0 else 0
        if strength < pot_odds * 0.8:
            return ("fold", 0) if "fold" in actions_list else ("check", 0)

    if strength > 0.70 and random.random() < diff["aggression"] and "raise" in actions_list:
        if diff["pot_odds_aware"]:
            sizing = pot * random.uniform(0.4, 1.0) + current_bet
        else:
            sizing = big_blind * random.uniform(2, 3) + current_bet
        return "raise", max(min_raise_to, min(int(sizing), max_raise_to))

    if strength > 0.55 and random.random() < diff["aggression"] * 0.5 and "raise" in actions_list:
        sizing = big_blind * random.uniform(2, 3) + current_bet
        return "raise", max(min_raise_to, min(int(sizing), max_raise_to))

    if call_amount == 0 and "check" in actions_list:
        return "check", 0
    if "call" in actions_list:
        return "call", 0
    if "check" in actions_list:
        return "check", 0
    return "fold", 0


def bot_decide_action(player, community_cards, pot, current_bet, min_raise_to,
                      max_raise_to, actions_list, call_amount, active_count,
                      dealer_distance, big_blind):
    """The correct play, then a difficulty-scaled chance of the wrong one."""
    diff = player.difficulty
    action, raise_to = _bot_correct_action(
        player, community_cards, pot, current_bet, min_raise_to, max_raise_to,
        actions_list, call_amount, active_count, dealer_distance, big_blind,
    )

    if random.random() < diff.get("mistake_chance", 0):
        if action == "fold" and call_amount > 0 and "call" in actions_list:
            return "call", 0            # overcalls junk, leaks chips
        if action == "raise":
            if "check" in actions_list:
                return "check", 0       # slow-plays a good hand, misses value
            if "call" in actions_list:
                return "call", 0
        if action == "call" and "fold" in actions_list and call_amount > 0:
            return "fold", 0            # folds a playable hand under pressure

    return action, raise_to


def calculate_side_pots(players) -> list[dict[str, Any]]:
    """Main pot plus one side pot per all-in level."""
    contributors = [p for p in players if p.total_bet > 0 and not p.is_eliminated]
    if not contributors:
        return [{"amount": 0, "eligible": []}]

    all_in_levels = sorted({p.total_bet for p in contributors if p.is_all_in})
    if not all_in_levels:
        total = sum(p.total_bet for p in contributors)
        return [{"amount": total, "eligible": [p.name for p in contributors if not p.is_folded]}]

    pots = []
    prev_level = 0
    for level in all_in_levels:
        if level <= prev_level:
            continue
        amount = 0
        eligible = []
        for p in contributors:
            contribution = min(p.total_bet, level) - min(p.total_bet, prev_level)
            amount += contribution
            if contribution > 0 and not p.is_folded and p.total_bet >= level:
                eligible.append(p.name)
        if amount > 0:
            pots.append({"amount": amount, "eligible": eligible})
        prev_level = level

    remaining = 0
    eligible = []
    for p in contributors:
        extra = p.total_bet - prev_level
        if extra > 0:
            remaining += extra
            if not p.is_folded:
                eligible.append(p.name)
    if remaining > 0:
        pots.append({"amount": remaining, "eligible": eligible})

    return pots or [{"amount": 0, "eligible": []}]


def resolve_showdown(players, community_cards) -> list[dict[str, Any]]:
    """Score every live hand and pay out every pot."""
    remaining = [p for p in players if not p.is_folded and not p.is_eliminated]

    if len(remaining) == 1:
        winner = remaining[0]
        total = sum(p.total_bet for p in players if not p.is_eliminated)
        winner.chips += total
        return [{
            "name": winner.name,
            "cards": [card_str(c) for c in winner.hole_cards],
            "hand_name": "Winner (others folded)",
            "won": total,
        }]

    hand_results = []
    for p in remaining:
        if len(community_cards) >= 3:
            rank = evaluator.evaluate(p.hole_cards, community_cards)
            hand_name = evaluator.class_to_string(evaluator.get_rank_class(rank))
        else:
            rank = 9999
            hand_name = "N/A"
        hand_results.append({
            "player": p,
            "rank": rank,
            "hand_name": hand_name,
            "cards": [card_str(c) for c in p.hole_cards],
            "won": 0,
        })

    for pot_info in calculate_side_pots(players):
        eligible = [r for r in hand_results if r["player"].name in pot_info["eligible"]]
        if not eligible:
            eligible = hand_results
        if not eligible:
            continue
        best = min(r["rank"] for r in eligible)
        winners = [r for r in eligible if r["rank"] == best]
        split, remainder = divmod(pot_info["amount"], len(winners))
        for i, w in enumerate(winners):
            award = split + (1 if i < remainder else 0)
            w["player"].chips += award
            w["won"] += award

    return [{
        "name": r["player"].name,
        "cards": r["cards"],
        "hand_name": r["hand_name"],
        "won": r["won"],
    } for r in hand_results]


def next_active_seat(players, current: int, offset: int = 1) -> int:
    """The next seat that has not been eliminated."""
    n = len(players)
    for i in range(1, n + 1):
        idx = (current + i * offset) % n
        if not players[idx].is_eliminated:
            return idx
    return current


# ~~~ the game ~~~
class PokerGame:
    """One poker session, driven a decision at a time.

    The activity calls:
        1. start()                        set up players
        2. await advance_to_agent()       bots act, cards come out
        3. get_turn_state()               what the agent must decide
        4. apply_choice(letter, ...)      apply it
        5. repeat 2-4 until is_game_over()
        6. get_result()

    Everything between step 2 and step 3 is Python. The agent only ever
    answers step 3, with a letter and a number.
    """

    def __init__(self, num_players: int, difficulty_key: str, game_mode: str,
                 num_hands: int = DEFAULT_CASH_HANDS, short_game: bool = False,
                 agent_name: str = "You", starting_chips: int = DEFAULT_CHIPS,
                 small_blind: int = DEFAULT_SMALL_BLIND, big_blind: int = DEFAULT_BIG_BLIND,
                 pace: bool = True) -> None:
        self._num_players = max(2, min(6, num_players))
        self._difficulty_key = difficulty_key
        self._difficulty = BOT_DIFFICULTIES[difficulty_key]
        self._game_mode = game_mode
        self._mode = GAME_MODES[game_mode]
        self._num_hands = num_hands if self._mode["fixed_hands"] else MAX_HANDS
        self._starting_chips = starting_chips
        self._short_game = short_game
        self._blind_increase_hands = (
            SHORT_BLIND_INCREASE_HANDS if short_game else BLIND_INCREASE_HANDS
        )
        self._agent_name = agent_name
        self._pace = pace

        self._players: list[Player] = []
        self._agent: Player | None = None
        self._dealer_idx = 0
        self.narrative = SessionNarrative()

        # per-hand
        self._hand_action_log: list[str] = []
        self._deck = None
        self._community_cards: list[int] = []
        self._pot = 0
        self._current_phase: str | None = None
        self._phase_index = 0
        self._chips_before = 0

        # betting round
        self._betting_current_bet = 0
        self._betting_min_raise_to = 0
        self._betting_last_raiser: int | None = None
        self._betting_last_raise_size = 0
        self._betting_acted: set[str] = set()
        self._betting_idx = 0
        self._betting_start = 0
        self._betting_max_iterations = 0
        self._betting_iterations = 0
        self._in_betting_round = False

        self._event_log: list[str] = []
        self._game_over = False
        self._game_result: dict[str, Any] | None = None
        self._hand_started = False

        self.state = TableState()
        self.state.small_blind = small_blind
        self.state.big_blind = big_blind

    # ~~~ public API ~~~
    def start(self) -> None:
        """Seat the agent and the bots, ready for the first hand."""
        bot_names = list(BOT_NAMES)
        random.shuffle(bot_names)

        self._players = [Player(self._agent_name, self._starting_chips, is_agent=True)]
        for i in range(self._num_players - 1):
            self._players.append(
                Player(bot_names[i], self._starting_chips, difficulty=self._difficulty)
            )
        random.shuffle(self._players)
        for i, p in enumerate(self._players):
            p.seat = i
        self._agent = next(p for p in self._players if p.is_agent)

        state = self.state
        state.players = self._players
        state.agent_name = self._agent_name
        state.bot_difficulty_name = self._difficulty["name"]
        state.game_mode = self._game_mode
        state.starting_chips = self._starting_chips
        if self._mode["fixed_hands"]:
            state.total_hands = self._num_hands
        state.status = "playing"

    def table_line(self) -> str:
        """One line naming the table, for the transcript."""
        names = ", ".join(p.name for p in self._players)
        label = self._mode["name"]
        if self._mode["fixed_hands"]:
            label += f" ({self._num_hands} hands)"
        if self._short_game:
            label += " [short]"
        return (
            f"{label} | {self._difficulty['name']} bots | "
            f"{self._num_players} players: {names} | "
            f"{format_chips(self._starting_chips)} chips each"
        )

    async def advance_to_agent(self) -> str:
        """Play bots and deal cards until the agent must decide, or the game ends."""
        self._event_log = []
        while not self._game_over:
            if not self._hand_started:
                if not self._can_start_new_hand():
                    self._finish_game()
                    break
                self._start_new_hand()
                if self._game_over:
                    break

            if self._in_betting_round:
                result = await self._continue_betting_round()
                if result == "need_agent":
                    break
                if result == "round_done":
                    self._advance_phase()
                    continue
                if result == "hand_over":
                    await self._resolve_current_hand()
                    continue
            else:
                if self._phase_index > 3:
                    await self._resolve_current_hand()
                    continue
                await self._begin_betting_round()

        return "\n".join(self._event_log)

    def get_turn_state(self) -> dict[str, Any] | None:
        """Everything the prompt builder needs, or None when it is not our turn."""
        if self._game_over or not self._in_betting_round or self._agent is None:
            return None
        actions, call_amount, min_r, max_r = get_legal_actions(
            self._agent, self._betting_current_bet, self._betting_min_raise_to,
            self.state.big_blind,
        )
        return {
            "player": self._agent,
            "players": self._players,
            "community_cards": self._community_cards,
            "pot": self._pot,
            "phase": self._current_phase,
            "hand_number": self.state.hand_number,
            "small_blind": self.state.small_blind,
            "big_blind": self.state.big_blind,
            "actions": actions,
            "call_amount": call_amount,
            "min_raise": min_r,
            "max_raise": max_r,
            "hand_action_log": self._hand_action_log,
            "session_context": self.narrative.build_context(2000),
        }

    def apply_choice(self, choice_letter: str, thinking: str = "", raise_amount: int = 0) -> str:
        """Apply the agent's fold/check/call/raise. Returns the action text."""
        if self._game_over or not self._in_betting_round or self._agent is None:
            return ""
        player = self._agent
        actions, call_amount, min_r, max_r = get_legal_actions(
            player, self._betting_current_bet, self._betting_min_raise_to,
            self.state.big_blind,
        )

        index = ord(choice_letter.upper()) - 65 if choice_letter else -1
        if 0 <= index < len(actions):
            action = actions[index]
        else:
            # An unusable letter must never cost chips: stand pat if we can.
            action = "check" if "check" in actions else "fold"

        if action == "raise":
            raise_amount = max(min_r, min(raise_amount, max_r))
        if thinking:
            self._log_thinking(thinking)

        action_text = self._apply_player_action(
            player, action, call_amount, raise_amount, min_r, max_r
        )
        self._hand_action_log.append(action_text)
        self._log_action(action_text)
        self._betting_acted.add(player.name)
        self.state.pot = self._pot
        self._betting_idx += 1
        return action_text

    def is_game_over(self) -> bool:
        return self._game_over

    def get_result(self) -> dict[str, Any]:
        if self._game_result:
            return self._game_result
        return {"result": "D", "result_text": "Game in progress", "summary": ""}

    def standing(self) -> str:
        """'winning', 'losing' or 'even' against the average live stack."""
        agent = self._agent
        if agent is None or agent.is_eliminated:
            return "losing"
        active = [p for p in self._players if not p.is_eliminated]
        if not active:
            return "even"
        average = sum(p.chips for p in active) / len(active)
        if agent.chips > average:
            return "winning"
        if agent.chips < average:
            return "losing"
        return "even"

    def record_loss(self) -> None:
        """Quitting mid-game counts as a loss."""
        self._game_over = True
        self.state.status = "finished"
        self.state.phase = "finished"
        agent = self._agent

        if self._mode["eliminates"]:
            text = f"Quit the tournament (had {format_chips(agent.chips)} chips)."
            self._game_result = {
                "result": "L",
                "result_text": text,
                "summary": self._build_session_summary(text),
            }
            return

        invested = self._starting_chips + agent.rebuys * self._starting_chips
        pnl = agent.chips - invested
        code = "W" if pnl > 0 else "L" if pnl < 0 else "D"
        sign = "+" if pnl > 0 else ""
        text = (
            f"Quit the cash game ({sign}{format_chips(pnl)} chips "
            f"after {self.state.hand_number} hands)."
        )
        self._game_result = {
            "result": code,
            "result_text": text,
            "summary": self._build_session_summary(text),
        }

    # ~~~ save and restore ~~~
    def save_data(self) -> dict[str, Any]:
        return {
            "game": "poker",
            "players": [{
                "name": p.name,
                "chips": p.chips,
                "is_agent": p.is_agent,
                "is_eliminated": p.is_eliminated,
                "seat": p.seat,
                "rebuys": p.rebuys,
            } for p in self._players],
            "num_players": self._num_players,
            "difficulty_key": self._difficulty_key,
            "game_mode": self._game_mode,
            "num_hands": self._num_hands,
            "short_game": self._short_game,
            "hand_number": self.state.hand_number,
            "dealer_idx": self._dealer_idx,
            "starting_chips": self._starting_chips,
            "small_blind": self.state.small_blind,
            "big_blind": self.state.big_blind,
            "agent_name": self._agent_name,
            "narrative_summary": "; ".join(self.narrative.entries[-5:]),
        }

    @classmethod
    def restore(cls, data: dict[str, Any], pace: bool = True) -> PokerGame:
        """Rebuild a saved game. Chips and seats survive; a resume deals fresh."""
        game = cls(
            data["num_players"], data["difficulty_key"], data["game_mode"],
            data["num_hands"], short_game=data.get("short_game", False),
            agent_name=data.get("agent_name", "You"),
            starting_chips=data.get("starting_chips", DEFAULT_CHIPS),
            small_blind=data.get("small_blind", DEFAULT_SMALL_BLIND),
            big_blind=data.get("big_blind", DEFAULT_BIG_BLIND),
            pace=pace,
        )
        game._players = []
        for saved in data["players"]:
            if saved["is_agent"]:
                player = Player(saved["name"], saved["chips"], is_agent=True)
            else:
                player = Player(saved["name"], saved["chips"], difficulty=game._difficulty)
            player.is_eliminated = saved["is_eliminated"]
            player.seat = saved["seat"]
            player.rebuys = saved.get("rebuys", 0)
            game._players.append(player)

        game._agent = next(p for p in game._players if p.is_agent)
        game._dealer_idx = data.get("dealer_idx", 0)

        state = game.state
        state.players = game._players
        state.agent_name = game._agent_name
        state.bot_difficulty_name = game._difficulty["name"]
        state.game_mode = game._game_mode
        state.starting_chips = game._starting_chips
        state.hand_number = data.get("hand_number", 0)
        if game._mode["fixed_hands"]:
            state.total_hands = game._num_hands
        state.status = "playing"
        return game

    # ~~~ logging ~~~
    def _log_action(self, text: str, hand_number: int | None = None) -> None:
        hand = hand_number if hand_number is not None else self.state.hand_number
        self.state.action_log.append({"hand": hand, "text": text})

    def _log_thinking(self, thinking: str) -> None:
        self.state.thinking_log.append(
            {"hand": self.state.hand_number, "thinking": thinking}
        )

    async def _pause(self, seconds: float) -> None:
        """Let a watcher follow the table. Async, so the agent stays responsive."""
        if self._pace and seconds > 0:
            await asyncio.sleep(seconds)

    # ~~~ hand lifecycle ~~~
    def _can_start_new_hand(self) -> bool:
        state = self.state
        if self._mode["eliminates"]:
            alive = [p for p in self._players if not p.is_eliminated]
            if len(alive) <= 1:
                return False
            if state.hand_number >= self._num_hands:
                return False
            return not self._agent.is_eliminated
        return state.hand_number < self._num_hands

    def _start_new_hand(self) -> None:
        state = self.state
        players = self._players

        if self._mode["rebuys"]:
            for p in players:
                if p.chips <= 0:
                    p.chips = self._starting_chips
                    p.rebuys += 1
                    p.is_eliminated = False
                    p.is_all_in = False
                    message = f"{p.name} rebuys for {format_chips(self._starting_chips)}"
                    self._log_action(message)
                    self._event_log.append(message)

        state.hand_number += 1
        hand_num = state.hand_number

        for p in players:
            if not p.is_eliminated:
                p.reset_for_hand()

        active = [p for p in players if not p.is_eliminated]
        if len(active) < 2:
            self._finish_game()
            return

        self._hand_action_log = []
        self._chips_before = self._agent.chips

        rising = self._mode["rising_blinds"]
        if rising and hand_num > 1 and (hand_num - 1) % self._blind_increase_hands == 0:
            state.small_blind = int(state.small_blind * BLIND_INCREASE_FACTOR)
            state.big_blind = int(state.big_blind * BLIND_INCREASE_FACTOR)
            message = f"Blinds increased to {state.small_blind}/{state.big_blind}"
            self._log_action(message, hand_num)
            self._event_log.append(message)

        self._event_log.append(
            f"--- Hand #{hand_num} | Blinds {state.small_blind}/{state.big_blind} ---"
        )

        state.phase = "preflop"
        state.community_cards = []
        state.pot = 0
        state.showdown_results = []
        state.reveal_cards = False
        self._pot = 0

        # Blinds. Heads-up, the dealer posts the small blind.
        if len(active) == 2:
            sb_idx = self._dealer_idx
            bb_idx = next_active_seat(players, self._dealer_idx)
        else:
            sb_idx = next_active_seat(players, self._dealer_idx)
            bb_idx = next_active_seat(players, sb_idx)

        blinds = ((sb_idx, state.small_blind, "SB"), (bb_idx, state.big_blind, "BB"))
        for idx, blind, label in blinds:
            player = players[idx]
            amount = min(blind, player.chips)
            player.chips -= amount
            player.current_bet = amount
            player.total_bet = amount
            if player.chips <= 0:
                player.is_all_in = True
            self._pot += amount
            message = f"{player.name} posts {label} {format_chips(amount)}"
            self._log_action(message, hand_num)
            self._hand_action_log.append(message)
            self._event_log.append(message)

        self._deck = Deck()
        for p in players:
            if not p.is_eliminated:
                p.hole_cards = self._deck.draw(2)

        agent = self._agent
        if agent and not agent.is_eliminated:
            message = f"Your cards: {cards_str(agent.hole_cards)}"
            self._log_action(message, hand_num)
            self._event_log.append(message)

        state.pot = self._pot
        state.dealer_index = self._dealer_idx

        self._community_cards = []
        self._current_phase = "preflop"
        self._phase_index = 0
        self._hand_started = True
        self._in_betting_round = False

    async def _begin_betting_round(self) -> None:
        state = self.state
        players = self._players
        n = len(players)

        phase_name = ["preflop", "flop", "turn", "river"][self._phase_index]
        self._current_phase = phase_name

        active = [
            p for p in players
            if not p.is_folded and not p.is_all_in and not p.is_eliminated
        ]
        if len(active) <= 1:
            non_folded = [p for p in players if not p.is_folded and not p.is_eliminated]
            if len(non_folded) <= 1:
                self._in_betting_round = False
                await self._resolve_current_hand()
                return
            # Everyone left is all-in: run the board out without betting.
            # _advance_phase deals for the current street and moves the index
            # on itself, so bumping it here too would skip a street.
            self._in_betting_round = False
            self._advance_phase()
            return

        current_bet = max((p.current_bet for p in players), default=0)

        if phase_name == "preflop":
            if n == 2:
                start = self._dealer_idx
            else:
                sb = next_active_seat(players, self._dealer_idx)
                bb = next_active_seat(players, sb)
                start = next_active_seat(players, bb)
        else:
            start = next_active_seat(players, self._dealer_idx)

        self._betting_current_bet = current_bet
        self._betting_min_raise_to = current_bet + state.big_blind
        self._betting_last_raiser = None
        self._betting_last_raise_size = state.big_blind
        self._betting_acted = set()
        self._betting_idx = start
        self._betting_start = start
        self._betting_max_iterations = n * 10
        self._betting_iterations = 0
        self._in_betting_round = True

    async def _continue_betting_round(self) -> str:
        """'need_agent', 'round_done' or 'hand_over'."""
        state = self.state
        players = self._players
        n = len(players)

        while self._betting_iterations < self._betting_max_iterations:
            self._betting_iterations += 1
            seat = self._betting_idx % n
            player = players[seat]

            if player.is_folded or player.is_all_in or player.is_eliminated or player.chips <= 0:
                self._betting_idx += 1
                continue

            # The round ends when action comes back to the last raiser, or to
            # where it started if nobody raised.
            if self._betting_last_raiser is not None:
                stop_at = self._betting_last_raiser
            else:
                stop_at = self._betting_start
            if player.name in self._betting_acted and seat == stop_at % n:
                self._in_betting_round = False
                return "round_done"

            actions, call_amount, min_r, max_r = get_legal_actions(
                player, self._betting_current_bet, self._betting_min_raise_to,
                state.big_blind,
            )
            state.current_actor = player.name

            if player.is_agent:
                return "need_agent"

            dealer_distance = (seat - self._dealer_idx) % n
            active_count = len([
                p for p in players
                if not p.is_folded and not p.is_all_in and not p.is_eliminated
            ])
            action, raise_to = bot_decide_action(
                player, self._community_cards, self._pot, self._betting_current_bet,
                min_r, max_r, actions, call_amount, active_count, dealer_distance,
                state.big_blind,
            )
            action_text = self._apply_player_action(
                player, action, call_amount, raise_to, min_r, max_r
            )
            self._hand_action_log.append(action_text)
            self._log_action(action_text)
            self._event_log.append(action_text)
            self._betting_acted.add(player.name)
            state.pot = self._pot
            await self._pause(BOT_ACTION_PAUSE)

            non_folded = [p for p in players if not p.is_folded and not p.is_eliminated]
            if len(non_folded) <= 1:
                self._in_betting_round = False
                return "hand_over"

            still_active = [
                p for p in players
                if not p.is_folded and not p.is_all_in
                and not p.is_eliminated and p.chips > 0
            ]
            settled = all(p.current_bet >= self._betting_current_bet for p in still_active)
            if len(still_active) <= 1 and settled:
                self._in_betting_round = False
                return "round_done"

            self._betting_idx += 1

        self._in_betting_round = False
        return "round_done"

    def _apply_player_action(self, player, action, call_amount, raise_to, min_r, max_r) -> str:
        if action == "fold":
            player.is_folded = True
            return f"{player.name}: fold"

        if action == "check":
            return f"{player.name}: check"

        if action == "call":
            amount = min(call_amount, player.chips)
            player.chips -= amount
            player.current_bet += amount
            player.total_bet += amount
            self._pot += amount
            if player.chips <= 0:
                player.is_all_in = True
                return f"{player.name}: call {format_chips(amount)} (all-in)"
            return f"{player.name}: call {format_chips(amount)}"

        if action == "raise":
            raise_to = max(min_r, min(raise_to, max_r))
            needed = min(raise_to - player.current_bet, player.chips)
            player.chips -= needed
            player.current_bet += needed
            player.total_bet += needed
            self._pot += needed
            raise_size = player.current_bet - self._betting_current_bet
            self._betting_current_bet = player.current_bet
            self._betting_last_raise_size = max(raise_size, self._betting_last_raise_size)
            self._betting_min_raise_to = (
                self._betting_current_bet + self._betting_last_raise_size
            )
            self._betting_last_raiser = player.seat
            self._betting_acted.clear()
            if player.chips <= 0:
                player.is_all_in = True
                return f"{player.name}: raise to {format_chips(player.current_bet)} (all-in)"
            return f"{player.name}: raise to {format_chips(player.current_bet)}"

        return f"{player.name}: check"

    def _advance_phase(self) -> None:
        state = self.state
        players = self._players

        non_folded = [p for p in players if not p.is_folded and not p.is_eliminated]
        if len(non_folded) <= 1:
            self._phase_index = 4
            self._in_betting_round = False
            return

        for p in players:
            p.current_bet = 0

        if self._phase_index == 0:
            self._phase_index = 1
            self._community_cards = self._deck.draw(3)
            state.phase = "flop"
            message = f"Flop: {cards_str(self._community_cards)}"
        elif self._phase_index == 1:
            self._phase_index = 2
            self._community_cards.extend(self._deck.draw(1))
            state.phase = "turn"
            message = f"Turn: {card_str(self._community_cards[-1])}"
        elif self._phase_index == 2:
            self._phase_index = 3
            self._community_cards.extend(self._deck.draw(1))
            state.phase = "river"
            message = f"River: {card_str(self._community_cards[-1])}"
        else:
            self._phase_index = 4
            self._in_betting_round = False
            return

        state.community_cards = list(self._community_cards)
        state.pot = self._pot
        self._log_action(message)
        self._event_log.append(message)
        self._in_betting_round = False

    async def _resolve_current_hand(self) -> None:
        state = self.state
        players = self._players
        hand_num = state.hand_number

        results = resolve_showdown(players, self._community_cards)

        if self._agent:
            self.narrative.add_hand(hand_num, build_hand_summary(
                self._agent, self._community_cards, self._hand_action_log,
                results, self._chips_before,
            ))

        state.phase = "showdown"
        state.reveal_cards = True
        state.showdown_results = results

        for r in results:
            if r["won"] > 0:
                message = (
                    f"{r['name']} wins {format_chips(r['won'])} with "
                    f"{r['hand_name']} ({' '.join(r['cards'])})"
                )
            else:
                message = f"{r['name']}: {r['hand_name']} ({' '.join(r['cards'])})"
            self._log_action(message, hand_num)
            self._event_log.append(message)

        await self._pause(SHOWDOWN_PAUSE)
        state.reveal_cards = False

        self._hand_started = False
        self._in_betting_round = False

        if self._mode["eliminates"]:
            for p in players:
                if p.chips <= 0 and not p.is_eliminated:
                    p.is_eliminated = True
                    message = f"{p.name} has been eliminated"
                    self._log_action(message)
                    self._event_log.append(message)

            alive = [p for p in players if not p.is_eliminated]
            if len(alive) <= 1:
                self._finish_game()
                return
            if self._agent.is_eliminated:
                message = "You have been eliminated, the game is over."
                self._log_action(message)
                self._event_log.append(message)
                self._finish_game()
                return
            self._dealer_idx = next_active_seat(players, self._dealer_idx)
        else:
            self._dealer_idx = (self._dealer_idx + 1) % len(players)

    def _finish_game(self) -> None:
        state = self.state
        players = self._players
        agent = self._agent
        self._game_over = True
        state.status = "finished"
        state.phase = "finished"

        if self._mode["eliminates"]:
            alive = [p for p in players if not p.is_eliminated]
            if len(alive) == 1:
                winner = alive[0]
            elif alive:
                winner = max(alive, key=lambda p: p.chips)
            else:
                winner = max(players, key=lambda p: p.chips)

            ranked = sorted(players, key=lambda p: p.chips, reverse=True)
            place = next(
                (i + 1 for i, p in enumerate(ranked) if p.is_agent), len(players)
            )

            if winner.name == agent.name:
                code = "W"
                text = f"Won the tournament with {format_chips(winner.chips)} chips."
            else:
                code = "L"
                ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(place, f"{place}th")
                text = f"Lost, {ordinal} place out of {len(players)}. Winner: {winner.name}."
            state.winner = winner.name
        else:
            pnl = {
                p.name: p.chips - (self._starting_chips + p.rebuys * self._starting_chips)
                for p in players
            }
            ranked = sorted(pnl.items(), key=lambda item: item[1], reverse=True)
            own = pnl.get(agent.name, 0)
            place = next(
                (i + 1 for i, (name, _) in enumerate(ranked) if name == agent.name),
                len(players),
            )

            if own > 0:
                code = "W"
                text = f"Won, +{format_chips(own)} profit over {state.hand_number} hands."
            elif own == 0:
                code = "D"
                text = f"Broke even over {state.hand_number} hands."
            else:
                code = "L"
                ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(place, f"{place}th")
                text = (
                    f"Lost, {ordinal} place ({format_chips(own)} chips "
                    f"over {state.hand_number} hands)."
                )
            state.winner = ranked[0][0] if ranked else ""
            state.profit_loss = pnl

        self._game_result = {
            "result": code,
            "result_text": text,
            "summary": self._build_session_summary(text),
        }
        self._event_log.append(text)

    def _build_session_summary(self, result_text: str) -> str:
        lines = [
            f"Poker {self._mode['name'].lower()} against "
            f"{self._difficulty['name']} bots ({self._num_players} players).",
            f"Played {self.state.hand_number} hands.",
            result_text,
        ]
        if self.narrative.entries:
            lines.append("Recent hands:")
            lines.extend(f"  {entry}" for entry in self.narrative.entries[-5:])
        return "\n".join(lines)
