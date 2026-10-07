"""Turn whatever the user pasted from their browser into jobinja.ir cookies.

Accepted: a `Cookie` header value (optionally prefixed with `cookie:`), a block of copied request headers,
Chrome DevTools "Copy as cURL" (bash or Windows cmd), "Copy as PowerShell", and JSON cookie exports
(a list of {name, value} objects, as produced by cookie-editor extensions).
"""

from __future__ import annotations

import json
import re
import shlex
from urllib.parse import urlsplit

_PS_COOKIE = re.compile(r'New-Object\s+System\.Net\.Cookie\(\s*"((?:[^"`]|`.)*)"\s*,\s*"((?:[^"`]|`.)*)"')
_HEADER_LINE = re.compile(r"(?im)^\s*cookie\s*:\s*(.+?)\s*$")
_URL = re.compile(r"https?://[^\s'\"^]+")


class CookieInputError(ValueError):
    pass


def _pairs(header: str) -> list[dict[str, str]]:
    cookies = []
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip():
            cookies.append({"name": name.strip(), "value": value.strip().strip('"')})
    return cookies


def _check_url(text: str) -> None:
    m = _URL.search(text)
    if m:
        host = (urlsplit(m.group(0)).hostname or "").lower()
        if host != "jobinja.ir" and not host.endswith(".jobinja.ir"):
            raise CookieInputError(f"That was copied from {host}, not jobinja.ir. Copy a request to jobinja.ir instead.")


def _from_curl(text: str) -> list[dict[str, str]]:
    if '^"' in text:  # Windows cmd flavour: ^ escapes every special character
        text = re.sub(r"\^(.)", r"\1", text)
    text = re.sub(r"\\\r?\n", " ", text).replace("$'", "'")
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise CookieInputError(f"Couldn't read the cURL command ({exc}). Paste it exactly as copied.") from exc
    found: list[dict[str, str]] = []
    for flag, value in zip(tokens, tokens[1:]):
        if flag in ("-b", "--cookie"):
            found += _pairs(value)
        elif flag in ("-H", "--header"):
            name, _, rest = value.partition(":")
            if name.strip().lower() == "cookie":
                found += _pairs(rest)
    return found


def _from_json(text: str) -> list[dict[str, str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CookieInputError("That looks like JSON but isn't valid JSON.") from exc
    if isinstance(data, dict):
        data = data.get("cookies", [data])
    cookies = []
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            continue
        domain = str(c.get("domain", "jobinja.ir")).lstrip(".").lower()
        if domain == "jobinja.ir" or domain.endswith(".jobinja.ir"):
            cookies.append({"name": str(c["name"]), "value": str(c["value"])})
    return cookies


def parse_cookie_text(text: str) -> list[dict[str, str]]:
    """Return [{name, value}] from any supported paste. Raises CookieInputError with a user-facing hint."""
    text = (text or "").strip()
    if not text:
        raise CookieInputError("Nothing was pasted.")
    if text[0] in "[{":
        cookies = _from_json(text)
    elif "System.Net.Cookie(" in text:
        _check_url(text)
        cookies = [{"name": re.sub(r"`(.)", r"\1", n), "value": re.sub(r"`(.)", r"\1", v)} for n, v in _PS_COOKIE.findall(text)]
    elif re.match(r"(?i)curl(\.exe)?\s", text):
        _check_url(text)
        cookies = _from_curl(text)
    elif m := _HEADER_LINE.search(text):
        cookies = _pairs(m.group(1))
    else:
        cookies = _pairs(text)
    if not cookies:
        raise CookieInputError(
            "No cookies found in what was pasted. In Chrome DevTools > Network, right-click the first jobinja.ir "
            "request and choose Copy > Copy as cURL (or copy the `cookie` request header)."
        )
    # Later duplicates win, matching how a browser's Cookie header is read.
    return list({c["name"]: c for c in cookies}.values())
