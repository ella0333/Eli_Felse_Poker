"""The engine is pure, so the rules are tested directly. The activity is
driven end to end on the MockProvider, with menu answers scripted as letters
exactly as a real model would send them."""

import json
import random

from treys import Card

from src.poker.engine import (
    GAME_MODES,
    MAX_HANDS,
    Player,
    PokerGame,
    build_hand_summary,
    calculate_side_pots,
    describe_hole_cards,
    get_legal_actions,
    resolve_showdown,
)


def hand(*cards):
    return [Card.new(c) for c in cards]


def make_player(name, chips, **state):
    """A player parked in a specific spot mid-hand."""
    player = Player(name, chips)
    for field, value in state.items():
        setattr(player, field, value)
    return player


async def play_out(game, letter="A"):
    """Take the first legal action every turn until the game ends."""
    turns = 0
    while not game.is_game_over():
        await game.advance_to_agent()
        if game.get_turn_state() is None:
            continue
        game.apply_choice(letter)
        turns += 1
    return turns


# ~~~ the rules ~~~
def test_legal_actions_without_a_bet_to_call():
    player = Player("A", 1000)
    actions, call, min_r, max_r = get_legal_actions(player, current_bet=0, min_raise_to=20, big_blind=20)
    assert actions == ["fold", "check", "raise"]
    assert call == 0 and min_r == 20 and max_r == 1000


def test_a_short_stack_can_shove_below_a_legal_raise():
    """25 chips can't make it to the 40 minimum, but going all-in is still a raise."""
    player = Player("A", 25)
    actions, call, min_r, max_r = get_legal_actions(player, current_bet=20, min_raise_to=40, big_blind=20)
    assert "raise" in actions
    assert min_r == max_r == 25


def test_calling_more_than_you_have_is_capped_at_your_stack():
    player = Player("A", 30)
    _, call, _, _ = get_legal_actions(player, current_bet=200, min_raise_to=400, big_blind=20)
    assert call == 30


def test_side_pots_split_at_each_all_in_level():
    small = make_player("Small", 0, total_bet=100, is_all_in=True)
    mid = make_player("Mid", 0, total_bet=300, is_all_in=True)
    big = make_player("Big", 500, total_bet=300)

    pots = calculate_side_pots([small, mid, big])
    assert [p["amount"] for p in pots] == [300, 400]
    assert pots[0]["eligible"] == ["Small", "Mid", "Big"]
    # Above the short stack's all-in, they are no longer playing for it.
    assert pots[1]["eligible"] == ["Mid", "Big"]


def test_a_folded_player_contributes_to_the_pot_but_cannot_win_it():
    folded = make_player("Folded", 0, total_bet=100, is_folded=True)
    live = make_player("Live", 400, total_bet=100)

    pots = calculate_side_pots([folded, live])
    assert pots[0]["amount"] == 200
    assert pots[0]["eligible"] == ["Live"]


def test_the_best_hand_takes_the_pot():
    winner = make_player("Winner", 0, total_bet=100, hole_cards=hand("Ah", "Ad"))
    loser = make_player("Loser", 0, total_bet=100, hole_cards=hand("2c", "7d"))
    board = hand("As", "Kd", "9c", "4h", "3s")

    results = resolve_showdown([winner, loser], board)
    by_name = {r["name"]: r for r in results}
    assert by_name["Winner"]["won"] == 200
    assert by_name["Loser"]["won"] == 0
    assert by_name["Winner"]["hand_name"] == "Three of a Kind"
    assert winner.chips == 200


def test_a_tie_splits_the_pot_and_the_odd_chip_goes_somewhere():
    one = make_player("One", 0, total_bet=51, hole_cards=hand("Ah", "Kh"))
    two = make_player("Two", 0, total_bet=50, hole_cards=hand("Ad", "Kd"))
    board = hand("As", "Ks", "9c", "4h", "3s")

    resolve_showdown([one, two], board)
    # Nothing is ever created or destroyed by a split.
    assert one.chips + two.chips == 101


def test_everyone_folding_wins_the_pot_without_a_showdown():
    winner = make_player("Winner", 0, total_bet=50, hole_cards=hand("2c", "7d"))
    folder = make_player("Folder", 0, total_bet=50, is_folded=True,
                         hole_cards=hand("Ah", "Ad"))

    results = resolve_showdown([winner, folder], [])
    assert len(results) == 1
    assert results[0]["hand_name"] == "Winner (others folded)"
    assert winner.chips == 100


def test_hole_cards_are_spelled_out_with_their_shape():
    assert "POCKET PAIR of Kings" in describe_hole_cards(hand("Kh", "Kd"))
    assert "SUITED, both Hearts" in describe_hole_cards(hand("Ah", "9h"))
    assert "OFFSUIT" in describe_hole_cards(hand("Ah", "9s"))
    assert "Ace of Hearts" in describe_hole_cards(hand("Ah", "9s"))


def test_a_folded_hand_still_reports_what_it_cost():
    """Folding keeps you out of the results, so the chip delta has to carry it."""
    agent = Player("You", 980, is_agent=True)
    agent.hole_cards = hand("2c", "7d")
    summary = build_hand_summary(agent, [], ["You: fold"], [], chips_before=1000)
    assert "folded" in summary
    assert "Lost 20" in summary


# ~~~ the game ~~~
async def test_a_cash_game_runs_the_hands_it_was_asked_for():
    random.seed(1)
    game = PokerGame(4, "medium", "cash", num_hands=6, pace=False)
    game.start()
    await play_out(game)

    assert game.is_game_over()
    assert game.state.hand_number == 6
    assert game.get_result()["result"] in {"W", "L", "D"}
    assert len(game.narrative.entries) == 6


async def test_a_tournament_ends_when_one_player_is_left():
    random.seed(3)
    game = PokerGame(3, "hard", "tournament", short_game=True, pace=False)
    game.start()
    await play_out(game, "B")

    assert game.is_game_over()
    agent = next(p for p in game.state.players if p.is_agent)
    alive = [p for p in game.state.players if not p.is_eliminated]
    # It ends one of three ways: one player left, the agent busted, or the
    # hand cap that stops two dust stacks trading blinds forever.
    assert len(alive) == 1 or agent.is_eliminated or game.state.hand_number >= MAX_HANDS
    assert game.get_result()["result"] in {"W", "L"}


async def test_every_board_dealt_is_a_legal_size():
    """All-in runouts once skipped a street and dealt a one-card flop."""
    for seed in (3, 5, 9, 11):
        random.seed(seed)
        game = PokerGame(3, "hard", "tournament", short_game=True, pace=False)
        game.start()
        await play_out(game, "B")
        for entry in game.narrative.entries:
            parts = entry.split("|")
            if len(parts) >= 4:  # this hand saw a board
                assert len(parts[1].split()) in (3, 4, 5), entry


async def test_a_short_game_raises_the_blinds_faster():
    random.seed(5)
    slow = PokerGame(3, "medium", "tournament", short_game=False, pace=False)
    random.seed(5)
    fast = PokerGame(3, "medium", "tournament", short_game=True, pace=False)
    assert fast._blind_increase_hands < slow._blind_increase_hands


async def test_a_cash_game_rebuys_instead_of_eliminating():
    assert GAME_MODES["cash"]["rebuys"] is True
    assert GAME_MODES["cash"]["eliminates"] is False
    assert GAME_MODES["tournament"]["eliminates"] is True


def test_an_unusable_letter_never_costs_chips():
    random.seed(2)
    game = PokerGame(2, "easy", "cash", num_hands=2, pace=False)
    game.start()
    game._start_new_hand()
    game._in_betting_round = True
    game._betting_current_bet = 0
    before = game._agent.chips
    game.apply_choice("Z")
    assert game._agent.chips == before


async def test_a_save_keeps_the_chips_and_resumes_the_table():
    random.seed(11)
    game = PokerGame(4, "easy", "cash", num_hands=6, pace=False)
    game.start()
    await game.advance_to_agent()
    game.get_turn_state()
    game.apply_choice("A")
    data = game.save_data()

    resumed = PokerGame.restore(data, pace=False)
    assert [p.chips for p in resumed.state.players] == [p.chips for p in game.state.players]
    assert [p.name for p in resumed.state.players] == [p.name for p in game.state.players]
    assert resumed.state.hand_number == game.state.hand_number
    await play_out(resumed)
    assert resumed.is_game_over()


# ~~~ the activity ~~~
async def test_the_activity_sits_under_the_shared_games_line(poker):
    activity, _ = poker
    assert activity.menu_group == "Play a Board or Card Game"
    assert activity.key == "poker"
    assert activity.requires == []


async def test_a_full_game_runs_from_scripted_menu_answers(poker, mock_provider, app):
    activity, ctx = poker
    # setup: 2 players, easy bots, cash game, full session
    mock_provider.feed({
        "thinking": "heads up, easy, cash",
        "num_players": 2, "difficulty": "A", "game_mode": "B", "session_length": "A",
    })
    mock_provider.feed({"thinking": "a short run", "num_hands": 10})
    # then fold everything
    for _ in range(200):
        mock_provider.feed({
            "thinking": "nothing here", "choice": "A",
            "return_to_menu": False, "raise_amount": "0",
        })

    note = await activity.run(ctx)

    assert "poker hands" in note
    assert app.stats.get("poker.games_played") == 1
    assert app.stats.get("poker.hands_played") == 10


async def test_config_can_fix_a_setting_and_drop_its_question(poker, mock_provider, app):
    """With every setting pinned, the setup menu never gets asked at all."""
    activity, ctx = poker
    app.config.activities["poker"] = {
        "pace_table": False, "players": 2, "difficulty": "easy",
        "game_mode": "cash", "hands": 10, "short_games": False,
    }
    for _ in range(200):
        mock_provider.feed({
            "thinking": "fold", "choice": "A",
            "return_to_menu": False, "raise_amount": "0",
        })

    await activity.run(ctx)

    # Every call was a betting decision; none carried a setup field.
    assert all("num_players" not in call["schema"]["properties"] for call in mock_provider.calls)
    assert all("num_hands" not in call["schema"]["properties"] for call in mock_provider.calls)
    assert app.stats.get("poker.hands_played") == 10


async def test_leaving_mid_game_can_save_and_resume(poker, mock_provider, app, tmp_path):
    activity, ctx = poker
    app.config.activities["poker"] = {
        "pace_table": False, "players": 2, "difficulty": "easy",
        "game_mode": "cash", "hands": 50, "short_games": False,
    }
    mock_provider.feed({
        "thinking": "I want to stop", "choice": "A",
        "return_to_menu": True, "raise_amount": "0",
    })
    mock_provider.feed({"thinking": "keep it for later", "choice": "B"})  # save and quit

    note = await activity.run(ctx)

    save = ctx.data_dir / "poker_save.json"
    assert save.exists()
    assert "Saved the poker game" in note
    saved = json.loads(save.read_text(encoding="utf-8"))
    assert saved["game"] == "poker"
    assert activity.get_status(ctx) == "saved game waiting"


async def test_quitting_mid_game_counts_as_a_loss(poker, mock_provider, app):
    activity, ctx = poker
    app.config.activities["poker"] = {
        "pace_table": False, "players": 2, "difficulty": "easy",
        "game_mode": "tournament", "short_games": False,
    }
    mock_provider.feed({
        "thinking": "done with this", "choice": "A",
        "return_to_menu": True, "raise_amount": "0",
    })
    mock_provider.feed({"thinking": "just go", "choice": "A"})  # quit

    note = await activity.run(ctx)

    assert "Quit the tournament" in note
    assert app.stats.get("poker.losses") == 1
    assert not (ctx.data_dir / "poker_save.json").exists()


async def test_the_dashboard_state_is_the_live_table(poker, mock_provider):
    activity, ctx = poker
    assert activity.dashboard_state(ctx) is None  # nothing running

    random.seed(4)
    activity._game = PokerGame(
        3, "easy", "cash", num_hands=2, agent_name=ctx.persona.name, pace=False
    )
    activity._game.start()
    state = activity.dashboard_state(ctx)

    assert state["game"] == "poker"
    assert len(state["players"]) == 3
    assert state["agent_name"] == "Testa"
    # Only the agent's own cards are ever in the payload before a showdown.
    activity._game._start_new_hand()
    state = activity.dashboard_state(ctx)
    shown = [p for p in state["players"] if p["hole_cards"]]
    assert len(shown) == 1 and shown[0]["is_agent"]


async def test_short_games_takes_a_yes_or_no_and_agent_means_ask(poker, mock_provider, app):
    """`short_games` is your switch, not a description of the agent's habits."""
    activity, ctx = poker
    base = {"pace_table": False, "players": 2, "difficulty": "easy", "game_mode": "cash",
            "hands": 10}

    app.config.activities["poker"] = {**base, "short_games": True}
    assert activity._short_games(ctx) is True
    app.config.activities["poker"] = {**base, "short_games": False}
    assert activity._short_games(ctx) is False
    # YAML writes `no` as a boolean, but a hand-typed word still reads.
    app.config.activities["poker"] = {**base, "short_games": "yes"}
    assert activity._short_games(ctx) is True
    # Absent, or explicitly "agent", means the setup menu asks.
    app.config.activities["poker"] = dict(base)
    assert activity._short_games(ctx) is None
    app.config.activities["poker"] = {**base, "short_games": "agent"}
    assert activity._short_games(ctx) is None


async def test_pinning_short_games_drops_the_session_length_question(poker, mock_provider, app):
    activity, ctx = poker
    app.config.activities["poker"] = {
        "pace_table": False, "players": 2, "difficulty": "easy",
        "game_mode": "cash", "hands": 10, "short_games": True,
    }
    for _ in range(200):
        mock_provider.feed({"thinking": "fold", "choice": "A",
                            "return_to_menu": False, "raise_amount": "0"})

    await activity.run(ctx)

    assert all("session_length" not in call["schema"]["properties"]
               for call in mock_provider.calls)
