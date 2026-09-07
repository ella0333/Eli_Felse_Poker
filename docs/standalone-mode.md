# Standalone mode

`standalone.py` plays poker with a model directly, without the Eli Felse base. Same
engine, same bots, same prompts; only the framework around them is missing.

## Setup

```bash
git clone https://github.com/ella0333/Eli_Felse_Poker.git
cd Eli_Felse_Poker
python -m venv .venv
.venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python standalone_setup.py
python standalone.py
```

### standalone_setup.py

It asks:

- **Where the model runs:** LM Studio, Ollama, or a paid API. Each has a default URL.
- **Model identifier**, e.g. `qwen3-8b` or `openai/gpt-4o-mini`.
- **Context tokens**, the clamp on every prompt. 36000 suits most local setups.
- **Games per session**, how many the model plays before the run stops. 0 for none.
- **Short games**, faster blinds and 10 to 25 hands instead of 10 to 100.
- **What the model is called** at the table.
- **System prompt**, default or your own.

It writes `standalone_config.yaml` and, for a paid API, a `.env` holding the key. The
config only ever stores the env-var *name*, never the key itself. Both are gitignored.

## Playing

Each game, the model answers a setup menu (players, difficulty, format), then a hand
count if it chose a cash game, then one action per betting decision. Everything else
is Python: the bots, the dealing, the pots, the showdown.

The table is served at http://127.0.0.1:8080 for as long as the session runs. It is
the same page the base's dashboard shows, polling the same endpoint, so what you see
standalone is what you would see installed.

## The session limit

The module's `daily_limit` becomes a per-run `session_limit`, because standalone has
no day to count against. The model is told how many games it has left before each
setup. At zero the run ends.

`return_to_menu` is dropped from the action schema. There is no menu to return to, so
a game always plays to its end and the budget is what stops the session.

## How it ends

- The game limit is spent.
- The model fails to answer three rounds in a row.
- You press `Ctrl+C`.

## Config format

```yaml
provider:
  base_url: "http://127.0.0.1:1234"
  api_key_env: ""            # name of the env var holding the key ("" = local)
  model: "qwen3-8b"
  max_context_tokens: 36000
  request_timeout: 300

model_params:
  default:
    temperature: 0.7
    top_p: 0.95
    max_tokens: 3000

poker:
  session_limit: 3           # games per run (0 = no limit)
  short_games: false
  agent_name: "Eli"
  starting_chips: 1000
  small_blind: 10
  big_blind: 20
  viewer_port: 8080

system_prompt: ""            # empty = use default
```

## Menus match the framework

Every menu is lettered and rendered exactly as the base renders it, and the schema
enum is the letters. A model that has played through the base sees the same frame
here, and a model that has only played here would see the same frame there.

## Context management

The session keeps its own message history and trims the oldest pair whenever the
total passes `max_context_tokens * 2` characters, which is the same approximation the
base uses. The system prompt is swapped for the table's guidance when a game starts,
so the model is told the format and the difficulty it is actually facing.
