from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from .config import Settings

log = logging.getLogger(__name__)


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: float) -> None: ...
    def clear(self) -> int: ...


class NullCache:
    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl: float) -> None:
        return None

    def clear(self) -> int:
        return 0


class DiskCache:
    def __init__(self, directory: str) -> None:
        import diskcache

        self._cache = diskcache.Cache(directory, size_limit=256 * 1024 * 1024)

    def get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._cache.set(key, value, expire=ttl)

    def clear(self) -> int:
        return self._cache.clear()


class RedisCache:
    prefix = "jobinja-mcp:"

    def __init__(self, url: str) -> None:
        import redis

        self._redis = redis.Redis.from_url(url)

    def get(self, key: str) -> Any | None:
        raw = self._redis.get(self.prefix + key)
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._redis.set(self.prefix + key, json.dumps(value, ensure_ascii=False), ex=max(1, int(ttl)))

    def clear(self) -> int:
        keys = list(self._redis.scan_iter(self.prefix + "*"))
        if keys:
            self._redis.delete(*keys)
        return len(keys)


def build_cache(settings: Settings) -> Cache:
    if not settings.cache_enabled:
        return NullCache()
    if settings.redis_url:
        try:
            return RedisCache(settings.redis_url)
        except Exception as exc:  # pragma: no cover - depends on environment
            log.warning("Redis cache unavailable (%s); falling back to disk cache", exc)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return DiskCache(str(settings.cache_dir))
