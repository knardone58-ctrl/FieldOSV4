"""Utility to ensure environment variables are loaded early."""

from pathlib import Path

from dotenv import load_dotenv


def init_env() -> None:
    """Loads `.env` file into the process environment if present."""
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
