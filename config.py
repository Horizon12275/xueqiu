from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements.txt installs it.
    load_dotenv = None


DEFAULT_CUBE_SYMBOL = "ZH2369777"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    cookie: str
    user_agent: str
    data_dir: Path
    timeout: float = 15.0
    holding_endpoint_templates: tuple[str, ...] = ()


def load_settings(data_dir_override: str | None = None) -> Settings:
    if load_dotenv is not None:
        load_dotenv()

    data_dir = Path(data_dir_override or os.getenv("XQ_DATA_DIR", "data")).expanduser()
    endpoint_templates = tuple(
        item.strip()
        for item in os.getenv("XQ_HOLDING_ENDPOINTS", "").split(",")
        if item.strip()
    )

    return Settings(
        cookie=os.getenv("XQ_COOKIE", "").strip(),
        user_agent=os.getenv("XQ_USER_AGENT", DEFAULT_USER_AGENT).strip(),
        data_dir=data_dir,
        holding_endpoint_templates=endpoint_templates,
    )


def require_cookie(settings: Settings) -> None:
    if not settings.cookie:
        raise ConfigError(
            "Missing XQ_COOKIE. Copy .env.example to .env and paste a logged-in "
            "xueqiu.com Cookie into XQ_COOKIE."
        )
