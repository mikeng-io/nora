"""Load and validate config.yaml."""

import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class Config:
    def __init__(self, raw: dict):
        self.signal_cli = raw["signal_cli"]
        self.honcho = raw["honcho"]
        self.llm = raw["llm"]
        self.buffer = raw["buffer"]
        self.tag = raw["tag"]
        self.logging = raw["logging"]

        self.db_path = Path(self.buffer["db_path"]).expanduser()
        self.log_path = Path(self.logging["path"]).expanduser()

        key_env = self.llm["api_key_env"]
        self.llm_api_key = os.environ.get(key_env)
        if not self.llm_api_key:
            raise RuntimeError(f"{key_env} is not set in the environment")


def load(path: str | Path | None = None) -> Config:
    path = Path(path).expanduser() if path else DEFAULT_PATH
    with open(path) as f:
        return Config(yaml.safe_load(f))
