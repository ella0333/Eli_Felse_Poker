"""Standalone setup wizard - configure the model connection for standalone poker.

Creates standalone_config.yaml (provider + model params + the per-session game
limit) and a .env file for the model's API key. Follows the same setup flow as
'elifelse init' in the base module: secrets live in .env, the config only
references env-var NAMES.

Run once before using standalone.py.
"""

import sys
from pathlib import Path

import yaml

MODULE_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_ROOT / "standalone_config.yaml"
ENV_PATH = MODULE_ROOT / ".env"


# ~~~ prompt helpers (same conventions as base WizardIO) ~~~────────────────

def _say(text: str) -> None:
    """Print a setup line, matching the base wizard's style."""
    try:
        print(f"[system] {text}")
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(f"[system] {text}".encode(enc, errors="replace").decode(enc))


def _text(prompt: str, default: str = "", show_default: bool = True) -> str:
    suffix = f" ({default})" if default and show_default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _yesno(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} ({hint}): ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        _say("please answer y or n")


def _integer(prompt: str, default: int | None = None, minimum: int = 0,
             show_default: bool = True) -> int:
    suffix = f" ({default})" if default is not None and show_default else ""
    while True:
        answer = input(f"{prompt}{suffix}: ").strip()
        if not answer and default is not None:
            return default
        if answer.lstrip("-").isdigit() and int(answer) >= minimum:
            return int(answer)
        _say(f"please enter a whole number >= {minimum}")


def _choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    """options = [(key, label), ...]; returns the chosen key."""
    _say(prompt)
    for i, (_, label) in enumerate(options, 1):
        _say(f"  {i}. {label}")
    keys = [k for k, _ in options]
    default_num = keys.index(default) + 1
    while True:
        answer = input(f"choose 1-{len(options)} ({default_num}): ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return keys[int(answer) - 1]
        _say(f"please enter a number from 1 to {len(options)}")


# ~~~ .env writer (matches base _write_env_stub) ~~~────────────────────

def _write_env_value(env_path: Path, var_name: str, value: str) -> None:
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if var_name in existing:
        _say(f"{env_path} already has {var_name} set.")
        return
    with env_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"{var_name}={value}\n")
    _say(f"wrote {var_name} to {env_path}")


def _write_env_stub(env_path: Path, var_name: str) -> None:
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if var_name in existing:
        _say(f"{env_path} already has {var_name} set.")
        return
    key = _text(
        f"Paste your model API key for {var_name} "
        "(will be stored in .env, never in config.yaml)"
    )
    if not key:
        _say(f"No key entered. Add it later to {env_path} as: {var_name}=your-key")
        key = "PASTE-YOUR-API-KEY-HERE"
    with env_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"{var_name}={key}\n")
    if key == "PASTE-YOUR-API-KEY-HERE":
        _say(f"wrote {env_path}. Open it and replace the placeholder with your key")
    else:
        _say(f"wrote {env_path}")


# ~~~ main wizard ~~~

def main():
    if CONFIG_PATH.exists():
        if not _yesno("standalone_config.yaml already exists. Overwrite it?", default=False):
            _say("left everything as it was; nothing written")
            return

    kind = _choice(
        "Where does the model run?",
        [
            ("lmstudio", "LM Studio on this computer (local, free)"),
            ("ollama", "Ollama on this computer (local, free)"),
            ("cloud", "A paid API (OpenRouter, OpenAI, Anthropic, etc.)"),
        ],
        default="lmstudio",
    )

    defaults = {
        "lmstudio": "http://127.0.0.1:1234",
        "ollama": "http://127.0.0.1:11434",
        "cloud": "https://api.openai.com/v1",
    }
    hints = {
        "lmstudio": "  (In LM Studio: Developer tab, look for the server URL, e.g. http://127.0.0.1:1234)",
        "ollama": "  (Ollama's default is http://127.0.0.1:11434)",
    }
    if kind in hints:
        _say(hints[kind])

    base_url = _text("Server URL", default=defaults[kind])

    model = _text(
        "Model identifier (e.g. 'qwen3-8b', 'openai/gpt-4o-mini')",
        default="default" if kind != "cloud" else "",
    ) or "default"

    api_key_env = "ELIFELSE_API_KEY" if kind == "cloud" else ""

    _say("")
    _say("Context clamp: how many tokens the framework reserves for every prompt.")
    _say("Set this at or below what your model/server actually supports.")
    max_context = _integer(
        "Context tokens (suggestion: 36000)", default=36000, minimum=2000,
        show_default=False,
    )

    _say("")
    _say("The model plays this many games back to back and then stops.")
    _say("Enter 0 for no limit (it will then run until you press Ctrl+C).")
    session_limit = _integer("Games per session", default=3, minimum=0)

    _say("")
    _say("A short game uses faster blinds and 10-25 hands instead of 10-100.")
    short_games = _yesno("Play short games?", default=False)

    agent_name = _text("What should the model be called at the table?", default="Eli")

    _say("")
    _say("The system prompt tells the model who it is while it plays.")
    use_default_prompt = _yesno("Use default system prompt?", default=True)

    system_prompt = ""
    if not use_default_prompt:
        _say("Enter your system prompt (end with an empty line):")
        lines = []
        while True:
            line = input("  ")
            if not line:
                break
            lines.append(line)
        system_prompt = "\n".join(lines)

    config = {
        "provider": {
            "kind": "openai_compat",
            "base_url": base_url,
            "api_key_env": api_key_env,
            "model": model,
            "max_context_tokens": max_context,
            "request_timeout": 300,
            "quirks": {
                "no_think_suffix": False,
            },
        },
        "model_params": {
            "default": {
                "temperature": 0.7,
                "top_p": 0.95,
                "max_tokens": 3000,
            },
        },
        "poker": {
            "session_limit": session_limit,
            "short_games": short_games,
            "agent_name": agent_name,
            "starting_chips": 1000,
            "small_blind": 10,
            "big_blind": 20,
            "viewer_port": 8080,
        },
        "system_prompt": system_prompt,
    }

    config_yaml = (
        "# Generated by standalone_setup.py. Safe to edit by hand.\n"
        + yaml.dump(config, default_flow_style=False, sort_keys=False)
    )
    CONFIG_PATH.write_text(config_yaml, encoding="utf-8")
    _say(f"wrote {CONFIG_PATH}")

    if api_key_env:
        _say("")
        _say(f"This is your key for {base_url}, the paid API you chose above.")
        _write_env_stub(ENV_PATH, api_key_env)

    _say("")
    _say("Done. Start playing with:  python standalone.py")
    _say("The table is at http://127.0.0.1:8080 while a game runs.")


if __name__ == "__main__":
    main()
