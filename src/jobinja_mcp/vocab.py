"""Resolve user-friendly names (Persian or English, any spelling) to Jobinja's canonical values."""

from __future__ import annotations

import difflib
import re
from typing import Iterable

_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا"})


def normalize(text: str) -> str:
    text = text.translate(_ARABIC_TO_PERSIAN).lower()
    text = re.sub(r"[\u200c\u200f\u200e\s،,/\\\-_()|]+", "", text)
    return text


def resolve(value: str, options: dict[str, str], what: str) -> str:
    """Map `value` to a canonical option.

    `options` maps every accepted alias (Persian name, English name, slug, id...) to the canonical value.
    """
    if value in options:
        return options[value]
    norm = {normalize(k): v for k, v in options.items()}
    key = normalize(value)
    if key in norm:
        return norm[key]
    contains = sorted({v for k, v in norm.items() if key and (key in k or k in key)})
    if len(contains) == 1:
        return contains[0]
    close = difflib.get_close_matches(key, list(norm), n=1, cutoff=0.75)
    if close:
        return norm[close[0]]
    suggestions = sorted(set(options.values()))[:60]
    raise ValueError(f"Unknown {what} '{value}'. Valid options include: {', '.join(suggestions)}")


def resolve_many(values: Iterable[str] | None, options: dict[str, str], what: str) -> list[str]:
    out: list[str] = []
    for v in values or []:
        canonical = resolve(v, options, what)
        if canonical not in out:
            out.append(canonical)
    return out
