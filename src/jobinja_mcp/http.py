from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import unquote

import httpx

from .cache import Cache, build_cache
from .config import Settings

log = logging.getLogger(__name__)

Params = Sequence[tuple[str, str]] | dict[str, Any] | None

_CSRF_META = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"')
_IS_AUTH = re.compile(r"isAuthenticated\s*=\s*!!parseInt\('(\d)'\)")


class JobinjaError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return {"error": str(self), "status": self.status, "details": self.payload}


class AuthRequired(JobinjaError):
    def __init__(self, message: str = "You must be logged in to Jobinja for this action. Call the `login` tool first.") -> None:
        super().__init__(message, status=401)


def _cache_key(method: str, url: str, params: Params, scope: str) -> str:
    if isinstance(params, dict):
        items = sorted((str(k), json.dumps(v, ensure_ascii=False, sort_keys=True)) for k, v in params.items())
    else:
        items = list(params or [])
    raw = json.dumps([scope, method, url, items], ensure_ascii=False)
    return "http:" + hashlib.sha256(raw.encode()).hexdigest()


class JobinjaHTTP:
    """Thin async wrapper around httpx that behaves like a logged-in browser tab."""

    def __init__(self, settings: Settings, cache: Cache | None = None) -> None:
        self.settings = settings
        self.cache = cache if cache is not None else build_cache(settings)
        self.csrf_token: str | None = None
        self.authenticated: bool | None = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.6",
            },
        )
        self.load_session()

    # ------------------------------------------------------------------ session
    def load_session(self) -> None:
        path: Path = self.settings.session_file
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Ignoring unreadable session file %s", path)
            return
        for c in data.get("cookies", []):
            self._client.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        self.csrf_token = data.get("csrf_token")
        self.authenticated = data.get("authenticated")

    def save_session(self) -> None:
        path: Path = self.settings.session_file
        path.parent.mkdir(parents=True, exist_ok=True)
        cookies = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "expires": c.expires}
            for c in self._client.cookies.jar
        ]
        payload = {"cookies": cookies, "csrf_token": self.csrf_token, "authenticated": self.authenticated}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8")

    def clear_session(self) -> None:
        self._client.cookies.clear()
        self.csrf_token = None
        self.authenticated = False
        if self.settings.session_file.exists():
            self.settings.session_file.unlink()

    def import_cookies(self, cookies: Iterable[dict[str, Any]], replace: bool = False) -> int:
        if replace:
            self._client.cookies.clear()
            self.csrf_token = None
            self.authenticated = None
        count = 0
        for c in cookies:
            domain = c.get("domain", "")
            if "jobinja" not in domain:
                continue
            self._client.cookies.set(c["name"], c["value"], domain=domain, path=c.get("path", "/"))
            count += 1
        return count

    def cookie_names(self) -> list[str]:
        return [c.name for c in self._client.cookies.jar]

    def cookie(self, name: str) -> str | None:
        for c in self._client.cookies.jar:
            if c.name == name:
                return c.value
        return None

    # ----------------------------------------------------------------- plumbing
    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self.settings.min_request_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            await self._throttle()
            try:
                resp = await self._client.request(method, url, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                log.info("%s %s failed (%s), retry %d", method, url, exc, attempt + 1)
            else:
                if resp.status_code in (429, 502, 503, 504) and attempt < self.settings.max_retries:
                    retry_after = resp.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * (attempt + 1)
                    await asyncio.sleep(min(delay, 15))
                    continue
                return resp
            await asyncio.sleep(1.0 * (attempt + 1))
        raise JobinjaError(f"Network error talking to Jobinja: {last_exc}") from last_exc

    def _absorb_html(self, html: str) -> None:
        m = _CSRF_META.search(html)
        if m:
            self.csrf_token = m.group(1)
        m = _IS_AUTH.search(html)
        if m:
            self.authenticated = m.group(1) == "1"

    @property
    def cache_scope(self) -> str:
        return "auth" if self.authenticated else "anon"

    # --------------------------------------------------------------------- HTML
    async def get_html(
        self,
        url: str,
        params: Params = None,
        *,
        cache_ttl: float | None = None,
        require_auth: bool = False,
    ) -> tuple[str, str]:
        """GET a page and return (html, final_url)."""
        use_cache = cache_ttl is not None and cache_ttl > 0 and not require_auth
        key = _cache_key("GET", url, params, self.cache_scope)
        if use_cache:
            hit = self.cache.get(key)
            if hit is not None:
                return hit["html"], hit["url"]

        resp = await self.request("GET", url, params=params, headers={"Accept": "text/html,application/xhtml+xml"})
        final_url = str(resp.url)
        if "/login/user" in resp.url.path and "/login/user" not in url:
            self.authenticated = False
            raise AuthRequired()
        if resp.status_code == 404:
            raise JobinjaError(f"Page not found: {final_url}", status=404)
        if resp.status_code >= 400:
            raise JobinjaError(f"Jobinja returned HTTP {resp.status_code} for {final_url}", status=resp.status_code)

        html = resp.text
        self._absorb_html(html)
        if require_auth and self.authenticated is False:
            raise AuthRequired()
        if use_cache:
            self.cache.set(key, {"html": html, "url": final_url}, cache_ttl)
        return html, final_url

    async def ensure_csrf(self) -> str:
        if not self.csrf_token:
            await self.get_html("/")
        if not self.csrf_token:
            raise JobinjaError("Could not obtain a CSRF token from Jobinja")
        return self.csrf_token

    # ---------------------------------------------------------------------- API
    def _api_headers(self, referer: str | None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.settings.base_url,
            "Referer": referer or self.settings.base_url + "/",
        }
        if self.csrf_token:
            headers["X-CSRF-TOKEN"] = self.csrf_token
        xsrf = self.cookie("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = unquote(xsrf)
        return headers

    async def api(
        self,
        method: str,
        path: str,
        *,
        params: Params = None,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        referer: str | None = None,
        cache_ttl: float | None = None,
        csrf: bool = True,
        require_auth: bool = False,
    ) -> Any:
        """Call a JSON endpoint. With `require_auth`, failures while logged out surface as AuthRequired
        (Jobinja answers account-only endpoints with 404/500 instead of 401 for guests)."""
        method = method.upper()
        use_cache = method == "GET" and cache_ttl is not None and cache_ttl > 0
        key = _cache_key(method, path, params, self.cache_scope)
        if use_cache:
            hit = self.cache.get(key)
            if hit is not None:
                return hit

        if csrf and method != "GET":
            await self.ensure_csrf()
        resp = await self.request(
            method,
            path,
            params=params,
            json=json_body,
            data=data,
            files=files,
            headers=self._api_headers(referer),
        )
        payload: Any
        try:
            payload = resp.json()
        except ValueError:
            payload = resp.text

        if resp.status_code == 401 or (resp.status_code == 403 and _looks_like_auth_error(payload)):
            self.authenticated = False
            raise AuthRequired()
        if resp.status_code == 419 and not files:
            # CSRF token expired: refresh once and retry.
            self.csrf_token = None
            await self.ensure_csrf()
            return await self.api(
                method, path, params=params, json_body=json_body, data=data, referer=referer, csrf=csrf, require_auth=require_auth
            )
        if resp.status_code >= 400 and require_auth and self.authenticated is not True:
            raise AuthRequired()
        if resp.status_code >= 400:
            raise JobinjaError(_error_message(payload, resp.status_code), status=resp.status_code, payload=payload)

        if use_cache:
            self.cache.set(key, payload, cache_ttl)
        return payload


def _looks_like_auth_error(payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    return "unauthenticated" in text.lower() or "login" in text.lower()


def _error_message(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return f"Jobinja error ({status}): {value}"
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            return f"Jobinja error ({status}): {data['message']}"
    return f"Jobinja returned HTTP {status}"
