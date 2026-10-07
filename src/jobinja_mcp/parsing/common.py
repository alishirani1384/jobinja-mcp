from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag

from ..utils import clean_text, parse_int, to_ascii_digits

_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text_of(node: Tag | None, *, multiline: bool = False) -> str:
    if node is None:
        return ""
    if not multiline:
        return clean_text(node.get_text(" ", strip=True)).replace("\n", " ")
    return rich_text(node)


def rich_text(node: Tag) -> str:
    """Convert an HTML fragment to readable plain text with bullet points and line breaks."""
    node = BeautifulSoup(str(node), "lxml")
    for br in node.find_all("br"):
        br.replace_with("\n")
    for li in node.find_all("li"):
        li.insert_before("\n• ")
    for tag in node.find_all(_BLOCK_TAGS):
        tag.insert_after("\n")
    return clean_text(node.get_text())


def attr(node: Tag | None, name: str) -> str | None:
    if node is None:
        return None
    value = node.get(name)
    if isinstance(value, list):
        return " ".join(value)
    return value


def parse_paginator(soup: BeautifulSoup) -> dict[str, Any]:
    pag = soup.select_one(".paginator")
    if pag is None:
        return {"page": 1, "total_pages": 1, "has_next": False}
    current = parse_int(text_of(pag.select_one("li.selected"))) or 1
    pages = [current]
    for a in pag.select("a[href]"):
        q = parse_qs(urlsplit(a["href"]).query)
        if "page" in q:
            n = parse_int(q["page"][0])
            if n:
                pages.append(n)
    return {"page": current, "total_pages": max(pages), "has_next": pag.select_one('a[rel="next"]') is not None}


def extract_init_states(html: str) -> list[dict[str, Any]]:
    """Return every JSON blob passed to Vue components via `init-state="..."` attributes."""
    states: list[dict[str, Any]] = []
    for m in re.finditer(r'(?:init-state|:init-state|initstate)\s*=\s*"([^"]*)"', html, flags=re.I):
        raw = html_lib.unescape(m.group(1))
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            states.append(value)
    for m in re.finditer(r"(?:init-state|initstate)\s*=\s*'([^']*)'", html, flags=re.I):
        try:
            value = json.loads(html_lib.unescape(m.group(1)))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            states.append(value)
    return states


def extract_json_ld(soup: BeautifulSoup, type_name: str | None = None) -> dict[str, Any] | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and (type_name is None or item.get("@type") == type_name):
                return item
    return None


def number_from(text: str) -> int | None:
    return parse_int(to_ascii_digits(text))
