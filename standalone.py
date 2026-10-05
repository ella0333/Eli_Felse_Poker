"""Standalone poker - play a session of Texas Hold'em with an LLM.

The model sets up a table, then plays hand after hand against the bots until
the session's game limit is spent or you press Ctrl+C. There is no menu to
return to, so a game always runs to its end. No Eli Felse Base required.

A live table is served at http://127.0.0.1:8080 while the session runs.

Usage:
    python standalone_setup.py   (once, to configure your model)
    python standalone.py         (play)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from dotenv import load_dotenv

from src.poker.engine import (
    BOT_DIFFICULTIES,
    DEFAULT_BIG_BLIND,
    DEFAULT_CASH_HANDS,
    DEFAULT_CHIPS,
    DEFAULT_SMALL_BLIND,
    GAME_MODES,
    PokerGame,
)
from src.poker.prompts import action_labels, build_turn_prompt, table_guidance

MODULE_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_ROOT / "standalone_config.yaml"
DASHBOARD_PAGE = MODULE_ROOT / "dashboard" / "index.html"

# The table viewer's port. Same as the base's dashboard, because standalone
# is what you run *instead of* the base, so the two never compete for it.
VIEWER_PORT = 8080

# Games a standalone session may play before it ends. 0 = no limit.
DEFAULT_SESSION_LIMIT = 3

# Consecutive failed rounds before the session gives up.
_MAX_CONSECUTIVE_FAILURES = 3

DEFAULT_SYSTEM_PROMPT = """\
You are an autonomous agent sitting down to play poker. You decide how to set \
up the table and how to play every hand.

You must respond in JSON, using exactly the fields the current step asks for. \
Your "thinking" field is your own reasoning, in your own voice."""

SETUP_SCHEMA = {
    "type": "object",
    "properties": {
        "thinking": {"type": "string"},
        "num_players": {"type": "integer"},
        "difficulty": {
            "type": "string",
            "enum": [chr(65 + i) for i in range(len(BOT_DIFFICULTIES))],
        },
        "game_mode": {
            "type": "string",
            "enum": [chr(65 + i) for i in range(len(GAME_MODES))],
        },
    },
    "required": ["thinking", "num_players", "difficulty", "game_mode"],
    "additionalProperties": False,
}

HANDS_SCHEMA = {
    "type": "object",
    "properties": {"thinking": {"type": "string"}, "num_hands": {"type": "integer"}},
    "required": ["thinking", "num_hands"],
    "additionalProperties": False,
}


def action_schema(actions: list[str], has_raise: bool) -> dict[str, Any]:
    """The per-turn action menu. No return_to_menu: there is no menu here."""
    properties: dict[str, Any] = {
        "thinking": {"type": "string"},
        "choice": {
            "type": "string",
            "enum": [chr(65 + i) for i in range(len(actions))],
        },
    }
    if has_raise:
        properties["raise_amount"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def menu_schema(letters: list[str]) -> dict[str, Any]:
    """A thinking field plus a choice locked to these letters, the base's shape."""
    return {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "choice": {"type": "string", "enum": list(letters)},
        },
        "required": ["thinking", "choice"],
        "additionalProperties": False,
    }


def build_menu(question: str, labels: list[str]) -> tuple[str, list[str]]:
    """Lettered menu text plus its letters, rendered like the base's menus."""
    letters = [chr(ord("A") + i) for i in range(len(labels))]
    lines = [question, ""]
    lines += [
        f"{letter}) {label}"
        for letter, label in zip(letters, labels, strict=True)
    ]
    return "\n".join(lines), letters


# Hosts that speak the Anthropic Messages API instead of OpenAI chat completions.
_ANTHROPIC_HOSTS = {"api.anthropic.com"}

# Regexes to extract the offending parameter from provider error messages.
_BAD_PARAM_RE = re.compile(
    r"[Uu]n(?:known|supported) (?:parameter|value):?\s*'?\"?(\w+)"
)
_BACKTICK_PARAM_RE = re.compile(r"`(\w+)`")

# Parameters that have a known alternate name on certain providers.
_PARAM_ALIASES: dict[str, str] = {
    "max_tokens": "max_completion_tokens",
}

# Never remove these from the payload, even on error.
_ESSENTIAL_PARAMS = {"model", "messages", "stream"}

# Maximum number of parameter-fix retries per call.
_MAX_PARAM_RETRIES = 6

# Regex to extract a JSON object from potentially wrapped text.
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Harmony-style channel tags some models leak into output.
_HARMONY_RE = re.compile(r"<\|[^|]*\|>")

# Placeholder content pattern.
_PLACEHOLDER_RE = re.compile(r"^[\[\(]?[.…]{2,}[\]\)]?\.?$")


def _print_system(text: str) -> None:
    """Print a framework status line, ASCII-safe."""
    line = f"[system] {text}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, errors="replace").decode(enc))


def _say(text: str) -> None:
    """Print agent-facing output, ASCII-safe."""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc))


def _strip_harmony_tags(text: str) -> str:
    return _HARMONY_RE.sub("", text).strip()


def _is_placeholder(text: str) -> bool:
    return not text.strip() or bool(_PLACEHOLDER_RE.match(text.strip()))


def _clean_thinking(text: str) -> str:
    """Strip mimicked '[Thinking: ...]' wrappers."""
    t = text.strip()
    nest = 0
    while t.startswith("[Thinking:"):
        t = t[len("[Thinking:"):].strip()
        nest += 1
    for _ in range(nest):
        if t.endswith("]"):
            t = t[:-1].strip()
    return t


def _parse_json(content: str) -> dict | None:
    """Parse JSON from model output, with regex fallback for chatty models."""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(content)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return None


def _validate_response(
    content: str, schema: dict, substantive: str | None = None
) -> dict | None:
    """Parse and validate model output against a schema.

    Returns the parsed dict with cleaned fields, or None if invalid.
    `substantive` names a field that must not come back as a placeholder.
    """
    parsed = _parse_json(content)
    if parsed is None:
        return None

    # Strip leaked formatting tags from all string fields.
    for key, val in list(parsed.items()):
        if isinstance(val, str):
            parsed[key] = _strip_harmony_tags(val)

    # Check required fields.
    for field in schema.get("required", []):
        if field not in parsed:
            return None

    # Enum fields must actually be in the enum.
    for field, spec in schema.get("properties", {}).items():
        allowed = spec.get("enum")
        if allowed and str(parsed.get(field, "")).strip().upper() not in allowed:
            return None

    if substantive and _is_placeholder(str(parsed.get(substantive, ""))):
        return None

    if isinstance(parsed.get("thinking"), str):
        parsed["thinking"] = _clean_thinking(parsed["thinking"])

    return parsed


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("Error: standalone_config.yaml not found.")
        print("Run 'python standalone_setup.py' first.")
        sys.exit(1)

    # Load .env for API key resolution (matches base pattern).
    env_path = MODULE_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _resolve_api_key(provider: dict) -> str:
    """Resolve the API key from an env var name (base pattern) or direct value (legacy)."""
    env_name = provider.get("api_key_env", "")
    if env_name:
        val = os.environ.get(env_name, "")
        if not val:
            print(f"Warning: api_key_env '{env_name}' is set but the environment "
                  f"variable is empty. Add it to your .env file.")
        return val
    return provider.get("api_key", "")


def _is_anthropic_host(base_url: str) -> bool:
    """Auto-detect Anthropic API by hostname."""
    host = (urlparse(base_url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _ANTHROPIC_HOSTS)


def _get_model_params(config: dict, model: str) -> dict:
    """Get model params, falling back to 'default' entry (matches base pattern)."""
    params = config.get("model_params", {})
    if isinstance(params, dict):
        if model in params:
            return params[model]
        if "default" in params:
            return params["default"]
        if "temperature" in params:
            return params
    return {"temperature": 0.7, "top_p": 0.95, "max_tokens": 3000}


def _build_openai_payload(
    model: str, messages: list, params: dict, schema: dict,
    blocked_params: set, param_renames: dict,
) -> dict:
    """Build an OpenAI-compatible chat completions payload."""
    candidates = {
        "temperature": params.get("temperature", 0.7),
        "top_p": params.get("top_p", 0.95),
        "max_tokens": params.get("max_tokens", 3000),
    }
    if params.get("repeat_penalty"):
        candidates["repeat_penalty"] = params["repeat_penalty"]
    if params.get("top_k"):
        candidates["top_k"] = params["top_k"]

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    for key, value in candidates.items():
        if key not in blocked_params:
            payload[key] = value

    payload["response_format"] = {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": schema},
    }

    for old, new in param_renames.items():
        if old in payload:
            payload[new] = payload.pop(old)

    return payload


def _build_anthropic_payload(
    model: str, messages: list, params: dict, schema: dict,
    blocked_params: set,
) -> dict:
    """Build an Anthropic Messages API payload with forced tool_use for structured output."""
    system_parts: list[str] = []
    chat_msgs: list[dict] = []
    for msg in messages:
        if msg["role"] == "system":
            content = msg["content"]
            if isinstance(content, str):
                system_parts.append(content)
        else:
            chat_msgs.append({"role": msg["role"], "content": msg["content"]})

    # Merge consecutive same-role messages (Anthropic requires alternation).
    merged: list[dict] = []
    for msg in chat_msgs:
        if merged and merged[-1]["role"] == msg["role"]:
            prev = merged[-1]["content"]
            cur = msg["content"]
            merged[-1]["content"] = f"{prev}\n\n{cur}"
        else:
            merged.append(dict(msg))

    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "(continue)"})
    if not merged:
        merged.append({"role": "user", "content": "(continue)"})

    payload = {
        "model": model,
        "messages": merged,
        "max_tokens": params.get("max_tokens", 3000),
        "stream": False,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    if "temperature" not in blocked_params:
        payload["temperature"] = params.get("temperature", 0.7)
    if "top_p" not in blocked_params and params.get("top_p"):
        payload["top_p"] = params["top_p"]
    if "top_k" not in blocked_params and params.get("top_k"):
        payload["top_k"] = params["top_k"]

    payload["tools"] = [{
        "name": "response",
        "description": "Respond with the required structured data.",
        "input_schema": schema,
    }]
    payload["tool_choice"] = {"type": "tool", "name": "response"}

    return payload


def _parse_openai_response(data: dict) -> str | None:
    """Extract content text from an OpenAI chat completions response."""
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        if not content and msg.get("reasoning_content"):
            content = msg["reasoning_content"]
        return content
    except (KeyError, IndexError, TypeError):
        return None


def _parse_anthropic_response(data: dict) -> str | None:
    """Extract content from an Anthropic Messages API response."""
    if isinstance(data, dict) and data.get("type") == "error":
        return None
    for block in data.get("content", []):
        if block.get("type") == "tool_use":
            return json.dumps(block.get("input", {}))
        if block.get("type") == "text":
            return block.get("text", "")
    return None


def _strip_model_prefix(model: str) -> str | None:
    """Strip publisher prefix (e.g. 'openai/gpt-4o' -> 'gpt-4o')."""
    if "/" not in model:
        return None
    stripped = model.split("/", 1)[1]
    return stripped if stripped != model else None


def _try_fix_param(
    error_text: str, payload: dict,
    blocked_params: set, param_renames: dict,
) -> bool:
    """Attempt to fix a parameter error in-place. Returns True if fixed."""
    m = _BAD_PARAM_RE.search(error_text)
    if not m:
        m = _BACKTICK_PARAM_RE.search(error_text)
    if not m:
        return False
    bad_param = m.group(1)
    if bad_param in _ESSENTIAL_PARAMS:
        return False

    alias = _PARAM_ALIASES.get(bad_param)
    if alias and bad_param not in param_renames:
        param_renames[bad_param] = alias
        if bad_param in payload:
            payload[alias] = payload.pop(bad_param)
        _print_system(f"Parameter '{bad_param}' not supported; using '{alias}' instead")
        return True

    if bad_param not in blocked_params:
        blocked_params.add(bad_param)
        payload.pop(bad_param, None)
        for orig, renamed in param_renames.items():
            if orig == bad_param:
                payload.pop(renamed, None)
        _print_system(f"Parameter '{bad_param}' not supported; removed")
        return True

    return False


class Session:
    """One standalone run: the message history plus the model connection."""

    def __init__(self, config: dict, client: httpx.AsyncClient) -> None:
        self.config = config
        self.client = client
        self.provider = config["provider"]
        self.is_anthropic = _is_anthropic_host(self.provider["base_url"])
        self.blocked_params: set[str] = set()
        self.param_renames: dict[str, str] = {}
        self.model_stripped: dict[str, str] = {}

        quirks = self.provider.get("quirks", {})
        self.no_think = quirks.get("no_think_suffix", False)
        if "qwen" in self.provider.get("model", "").lower() and not self.no_think:
            self.no_think = True
            _print_system("Qwen model detected, auto-enabled /no_think suffix")

        system_prompt = config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def set_guidance(self, guidance: str) -> None:
        """Swap the mode guidance appended to the system prompt."""
        base = self.config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        self.messages[0] = {"role": "system", "content": f"{base}\n\n{guidance}"}

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self._trim()

    def _trim(self) -> None:
        max_context = self.provider.get("max_context_tokens", 36000)
        total = sum(len(str(m["content"])) for m in self.messages)
        while total > max_context * 2 and len(self.messages) > 5:
            self.messages.pop(1)
            self.messages.pop(1)
            total = sum(len(str(m["content"])) for m in self.messages)

    async def ask(
        self, prompt: str, schema: dict, substantive: str | None = None
    ) -> dict | None:
        """Send one prompt and get back a validated dict, or None on failure."""
        content = prompt + (" /no_think" if self.no_think else "")
        self.messages.append({"role": "user", "content": content})
        self._trim()
        result = await self._call(schema, substantive)
        if result is not None:
            echo = {k: v for k, v in result.items() if k != "thinking"}
            self.messages.append({"role": "assistant", "content": json.dumps(echo)})
        return result

    async def _call(self, schema: dict, substantive: str | None) -> dict | None:
        model = self.provider["model"]
        params = _get_model_params(self.config, model)
        api_key = _resolve_api_key(self.provider)
        effective_model = self.model_stripped.get(model, model)

        if self.is_anthropic:
            base_url = self.provider["base_url"].rstrip("/")
            url = f"{base_url}/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            if api_key:
                headers["x-api-key"] = api_key
            payload = _build_anthropic_payload(
                effective_model, self.messages, params, schema, self.blocked_params,
            )
        else:
            base_url = self.provider["base_url"].rstrip("/")
            if not base_url.endswith("/v1"):
                base_url += "/v1"
            url = f"{base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = _build_openai_payload(
                effective_model, self.messages, params, schema,
                self.blocked_params, self.param_renames,
            )

        timeout = self.provider.get("request_timeout", 300)

        for _attempt in range(_MAX_PARAM_RETRIES):
            try:
                resp = await self.client.post(
                    url, json=payload, headers=headers, timeout=timeout
                )
            except httpx.HTTPError as e:
                _print_system(f"Connection error: {e}")
                return None

            if resp.status_code in (400, 404):
                error_text = resp.text[:500]
                error_lower = error_text.lower()

                if ("invalid model" in error_lower
                        or "not_found" in error_lower
                        or "could not resolve" in error_lower):
                    stripped = _strip_model_prefix(payload.get("model", ""))
                    if stripped:
                        _print_system(
                            f"Model '{payload['model']}' rejected; retrying as '{stripped}'"
                        )
                        self.model_stripped[model] = stripped
                        payload["model"] = stripped
                        continue
                    break

                if ("unknown parameter" in error_lower
                        or "unsupported parameter" in error_lower
                        or "unsupported value" in error_lower
                        or "deprecated" in error_lower
                        or "not supported" in error_lower):
                    if _try_fix_param(
                        error_text, payload, self.blocked_params, self.param_renames
                    ):
                        continue
                    break

                break

            if resp.status_code != 200:
                _print_system(f"LLM error {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()

            if isinstance(data, dict) and "error" in data and "choices" not in data:
                err = data["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                _print_system(f"LLM error: {msg[:200]}")
                return None

            if self.is_anthropic:
                content = _parse_anthropic_response(data)
            else:
                content = _parse_openai_response(data)

            if content is None:
                _print_system("Unexpected response shape")
                return None

            result = _validate_response(content, schema, substantive)
            if result is not None:
                return result

            _print_system("Response rejected (malformed/missing fields), retrying...")
            if self.is_anthropic:
                payload = _build_anthropic_payload(
                    effective_model, self.messages, params, schema, self.blocked_params,
                )
            else:
                payload = _build_openai_payload(
                    effective_model, self.messages, params, schema,
                    self.blocked_params, self.param_renames,
                )

        return None

    async def choose(self, question: str, labels: list[str]) -> int | None:
        """Lettered menu, exactly like the base's ctx.choose. Returns the index."""
        menu_text, letters = build_menu(question, labels)
        _say(f"\n{menu_text}")
        result = await self.ask(menu_text, menu_schema(letters))
        if result is None:
            return None
        letter = str(result.get("choice", "")).strip().upper()
        if result.get("thinking"):
            _say(f"\nThinking: {result['thinking']}")
        if letter not in letters:
            return None
        _say(f"Choice: {letter}")
        return letters.index(letter)


# ── the session budget ──────────────────────────────────────────────────────

class Budget:
    """Games this session may play, the standalone stand-in for the
    module's daily limit. A limit of 0 or less means no limit at all."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    @property
    def remaining(self) -> int | None:
        """Games left, or None when there is no cap."""
        if self.limit <= 0:
            return None
        return max(0, self.limit - self.used)

    @property
    def spent_out(self) -> bool:
        return self.remaining == 0

    def spend(self) -> None:
        """One finished game, one game off the session budget."""
        self.used += 1

    def line(self) -> str:
        """The budget note the agent reads before it sets up a game."""
        remaining = self.remaining
        if remaining is None:
            return ""
        if remaining <= 0:
            return "\n\n[You have no games left this session.]"
        plural = "s" if remaining != 1 else ""
        return (
            f"\n\n[You have {remaining} game{plural} left this session. "
            "Each game you play uses one.]"
        )

    def status(self) -> str:
        """The framework's own line about where the budget stands."""
        remaining = self.remaining
        if remaining is None:
            return f"Game {self.used + 1} (no session limit)"
        plural = "s" if remaining != 1 else ""
        return (
            f"Game {self.used + 1} of {self.limit} "
            f"({remaining} game{plural} left)"
        )


# ~~~ the table viewer ~~~
class _ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class TableViewer:
    """The same page the base's dashboard serves, on its own port.

    Standalone has no dashboard to plug into, so the module carries a small
    server of its own. It answers exactly the one endpoint the page polls.
    """

    def __init__(self, state_source, port: int = VIEWER_PORT) -> None:
        self.state_source = state_source
        self.port = port
        self._server: _ThreadedServer | None = None

    def start(self) -> None:
        source = self.state_source
        page = DASHBOARD_PAGE.read_bytes() if DASHBOARD_PAGE.exists() else b"<h1>no page</h1>"

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_GET(self):  # noqa: N802
                if self.path.startswith("/api/module-state"):
                    body = json.dumps(source() or {}, default=str).encode()
                    content_type = "application/json"
                elif self.path in ("/", "/index.html"):
                    body = page
                    content_type = "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._server = _ThreadedServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            # A viewer that cannot bind must say so. Silently serving nothing
            # once left a blank page up for a whole session.
            _print_system(f"table viewer could not start on port {self.port}: {e}")
            return
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        _print_system(f"table viewer: http://127.0.0.1:{self.port}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None


# ~~~ one game ~~~
async def run_game(session: Session, settings: dict, budget: Budget, viewer_slot: dict) -> bool:
    """Set up a table and play it out. False when the session should stop."""
    mode_keys = list(GAME_MODES)
    difficulty_keys = list(BOT_DIFFICULTIES)

    setup_prompt = "\n".join([
        "Set up your poker game (Texas Hold'em).",
        "",
        "Number of Players: choose 2-6 (includes you, default 4)",
        "",
        "Difficulty:",
        *[f"  {chr(65 + i)}. {v['menu']}" for i, v in enumerate(BOT_DIFFICULTIES.values())],
        "",
        "Game Mode:",
        *[f"  {chr(65 + i)}. {v['menu']}" for i, v in enumerate(GAME_MODES.values())],
    ]) + budget.line()

    _say(f"\n{setup_prompt}")
    answer = await session.ask(setup_prompt, SETUP_SCHEMA)
    if answer is None:
        return False
    if answer.get("thinking"):
        _say(f"\nThinking: {answer['thinking']}")

    players = _clamp(answer.get("num_players"), 2, 6, 4)
    difficulty = _letter_to_key(answer.get("difficulty"), difficulty_keys)
    game_mode = _letter_to_key(answer.get("game_mode"), mode_keys)
    short_game = bool(settings.get("short_games", False))

    num_hands = DEFAULT_CASH_HANDS
    if GAME_MODES[game_mode]["fixed_hands"]:
        top = 25 if short_game else 100
        default_hands = 20 if short_game else DEFAULT_CASH_HANDS
        hands_prompt = (
            f"How many hands would you like to play? "
            f"(choose 10-{top}, default {default_hands})"
        )
        _say(f"\n{hands_prompt}")
        reply = await session.ask(hands_prompt, HANDS_SCHEMA)
        if reply is None:
            return False
        num_hands = _clamp(reply.get("num_hands"), 10, top, default_hands)

    game = PokerGame(
        players, difficulty, game_mode, num_hands, short_game=short_game,
        agent_name=settings.get("agent_name", "You"),
        starting_chips=int(settings.get("starting_chips", DEFAULT_CHIPS)),
        small_blind=int(settings.get("small_blind", DEFAULT_SMALL_BLIND)),
        big_blind=int(settings.get("big_blind", DEFAULT_BIG_BLIND)),
    )
    game.start()
    viewer_slot["game"] = game
    session.set_guidance(table_guidance(game_mode, BOT_DIFFICULTIES[difficulty]["name"], short_game))

    _print_system(game.table_line())
    _say("  " + "-" * 60)

    failures = 0
    while not game.is_game_over():
        events = await game.advance_to_agent()
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

        _say(f"\n{prompt}")
        reply = await session.ask(prompt, action_schema(turn["actions"], "raise" in turn["actions"]))
        if reply is None:
            failures += 1
            if failures >= _MAX_CONSECUTIVE_FAILURES:
                _print_system("the model stopped answering, ending the session")
                return False
            continue
        failures = 0
        if reply.get("thinking"):
            _say(f"\nThinking: {reply['thinking']}")

        action_text = game.apply_choice(
            reply.get("choice", ""),
            thinking=reply.get("thinking", ""),
            raise_amount=_raise_amount(reply.get("raise_amount")),
        )
        _say(f"Action: {action_text}")

    result = game.get_result()
    _say("  " + "-" * 60)
    _print_system(result["result_text"])
    budget.spend()
    return True


def _letter_to_key(letter: Any, keys: list[str]) -> str:
    index = ord(str(letter).upper()) - 65 if letter else -1
    return keys[index] if 0 <= index < len(keys) else keys[0]


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return fallback


def _raise_amount(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


# ~~~ the session ~~~
async def run_session(config: dict) -> None:
    settings = config.get("poker", {})
    budget = Budget(int(settings.get("session_limit", DEFAULT_SESSION_LIMIT)))
    viewer_slot: dict[str, Any] = {"game": None}

    viewer = TableViewer(
        lambda: viewer_slot["game"].state.to_dict() if viewer_slot["game"] else None,
        port=int(settings.get("viewer_port", VIEWER_PORT)),
    )
    viewer.start()

    async with httpx.AsyncClient() as client:
        session = Session(config, client)
        try:
            while not budget.spent_out:
                _print_system(budget.status())
                if not await run_game(session, settings, budget, viewer_slot):
                    break
        finally:
            viewer.stop()

    _print_system(f"session over, {budget.used} game(s) played")


def main():
    print("\n+-------------------------------------+")
    print("|   Poker - Standalone Runner         |")
    print("+-------------------------------------+")
    config = load_config()
    try:
        asyncio.run(run_session(config))
    except KeyboardInterrupt:
        print()
        _print_system("stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
