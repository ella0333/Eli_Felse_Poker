"""What the agent reads: the table guidance, and the situation each turn."""

from __future__ import annotations

from .engine import (
    GAME_MODES,
    cards_long,
    cards_str,
    describe_hole_cards,
    describe_made_hand,
    format_chips,
)

ACTION_LABELS = {
    "fold": "Fold",
    "check": "Check",
    "call": "Call ({call} to call)",
    "raise": "Raise (min: {min_raise}, max: {max_raise})",
}


def mode_guidance(game_mode: str, short_game: bool) -> str:
    if GAME_MODES[game_mode]["eliminates"]:
        if short_game:
            return (
                "This is a turbo tournament. Play continues until one player has all the "
                "chips, and the blinds increase every few hands rather than slowly. Your "
                "stack will shrink fast relative to the blinds, so folding and waiting for "
                "premium hands will blind you out. Commit earlier and more often than you "
                "normally would, and take reasonable risks while your stack still has fold "
                "equity."
            )
        return (
            "This is a tournament game. Play continues until one player has all the chips. "
            "Blinds increase over time, so survival matters. Don't risk your entire stack "
            "on marginal hands."
        )

    text = (
        "This is a cash game. You are playing a set number of hands and tracking profit "
        "and loss. Focus on maximizing your profits over the session."
    )
    if short_game:
        text += " This is a short session, so don't wait too long to get involved in hands."
    return text


def table_guidance(game_mode: str, difficulty_name: str, short_game: bool) -> str:
    """The block appended to the agent's own identity prompt while it plays."""
    short_tip = ""
    if short_game:
        short_tip = (
            "\n- The blinds rise fast here, so the advice above about folding weak hands "
            "loosens as your stack gets short. Once you are down to a handful of big "
            "blinds, waiting costs more than playing"
        )

    return f"""You are playing Texas Hold'em Poker against bot players at {difficulty_name} difficulty.

{mode_guidance(game_mode, short_game)}

Hand rankings (highest to lowest):
Royal Flush, Straight Flush, Four of a Kind, Full House, Flush, Straight, Three of a Kind, Two Pair, One Pair, High Card

Betting actions:
- Fold: Give up your hand and any chips you've bet
- Check: Pass the action (only if there is no bet to call)
- Call: Match the current bet
- Raise: Increase the bet (you'll specify the amount)

Strategy tips:
- Position matters, acting later gives you more information
- Don't play too many hands, fold weak starting hands
- Pay attention to pot odds when deciding to call
- Vary your play to be unpredictable
- Pay attention to your chip stack relative to the blinds{short_tip}

The bot players are not real people, and no real money is involved."""


def action_labels(actions, call_amount, min_raise, max_raise) -> list[str]:
    """The action menu, in the order the engine offers them."""
    return [
        ACTION_LABELS[action].format(
            call=format_chips(call_amount),
            min_raise=format_chips(min_raise),
            max_raise=format_chips(max_raise),
        )
        for action in actions
    ]


def build_turn_prompt(turn) -> str:
    """The hand as it stands, everything the agent needs to pick an action."""
    player = turn["player"]
    lines = [
        f"Hand #{turn['hand_number']} | Phase: {turn['phase']} | "
        f"Blinds: {turn['small_blind']}/{turn['big_blind']}",
        "",
        f"Your hole cards: {cards_str(player.hole_cards)}",
        f"  = {describe_hole_cards(player.hole_cards)}",
    ]

    community = turn["community_cards"]
    if community:
        lines.append(f"Community cards: {cards_str(community)}")
        lines.append(f"  = {cards_long(community)}")
        made = describe_made_hand(player.hole_cards, community)
        if made:
            lines.append(f"Your best hand right now: {made}")
    else:
        lines.append("Community cards: (none yet)")

    lines += [
        "",
        f"Pot: {format_chips(turn['pot'])} chips",
        f"Your chips: {format_chips(player.chips)} | "
        f"Your bet this round: {format_chips(player.current_bet)}",
        "",
        "Players:",
    ]
    for other in turn["players"]:
        if other.is_eliminated:
            continue
        status = "folded" if other.is_folded else "all-in" if other.is_all_in else "active"
        marker = " (you)" if other.name == player.name else ""
        lines.append(
            f"  {other.name}{marker} - {format_chips(other.chips)} chips - "
            f"{status} - bet {format_chips(other.current_bet)}"
        )

    if turn["hand_action_log"]:
        lines += ["", "Actions this hand:"]
        lines += [f"  {entry}" for entry in turn["hand_action_log"][-15:]]

    if turn["session_context"]:
        lines += ["", "Session history (your memory of previous hands):", turn["session_context"]]

    return "\n".join(lines)
