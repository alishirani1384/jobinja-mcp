from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def _default_data_dir() -> Path:
    return Path(os.environ.get("JOBINJA_DATA_DIR") or Path.home() / ".jobinja-mcp")


@dataclass
class Settings:
    base_url: str = "https://jobinja.ir"
    data_dir: Path = field(default_factory=_default_data_dir)
    email: str | None = None
    password: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )
    timeout: float = 30.0
    min_request_interval: float = 0.4
    max_retries: int = 3
    cache_enabled: bool = True
    cache_ttl: float = 600.0
    redis_url: str | None = None
    http_auth_token: str | None = None
    browser_headless: bool = False

    @property
    def session_file(self) -> Path:
        return self.data_dir / "session.json"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=os.environ.get("JOBINJA_BASE_URL", "https://jobinja.ir").rstrip("/"),
            data_dir=_default_data_dir(),
            email=os.environ.get("JOBINJA_EMAIL") or None,
            password=os.environ.get("JOBINJA_PASSWORD") or None,
            timeout=_env_float("JOBINJA_TIMEOUT", 30.0),
            min_request_interval=_env_float("JOBINJA_MIN_REQUEST_INTERVAL", 0.4),
            max_retries=int(os.environ.get("JOBINJA_MAX_RETRIES", "3")),
            cache_enabled=_env_bool("JOBINJA_CACHE", True),
            cache_ttl=_env_float("JOBINJA_CACHE_TTL", 600.0),
            redis_url=os.environ.get("JOBINJA_REDIS_URL") or None,
            http_auth_token=os.environ.get("JOBINJA_MCP_AUTH_TOKEN") or None,
            browser_headless=_env_bool("JOBINJA_BROWSER_HEADLESS", False),
        )
