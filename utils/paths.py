from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_state_dir


APP_NAME = "gemma-local"


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    return Path(user_data_dir(APP_NAME))


def state_dir() -> Path:
    """Logs and other volatile runtime state (XDG state home)."""
    return Path(user_state_dir(APP_NAME))


def server_log_path() -> Path:
    """Server/llama.cpp log, written instead of the terminal while the TUI runs.

    Literal ``~/.local/state/gemma/server.log`` on Linux (the user-facing
    location), independent of the app's data-dir name.
    """
    return Path(user_state_dir("gemma")) / "server.log"
