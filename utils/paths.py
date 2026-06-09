from pathlib import Path

from platformdirs import user_config_dir, user_data_dir


APP_NAME = "gemma-local"


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    return Path(user_data_dir(APP_NAME))
