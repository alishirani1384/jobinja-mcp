from __future__ import annotations

import re
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

BASE_URL = "https://jobinja.ir"

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_WS = re.compile(r"[ \t\r\f\v\u00a0]+")
_MULTI_NL = re.compile(r"\n{3,}")
_JOB_PATH = re.compile(r"^/companies/(?P<company>[^/]+)/jobs/(?P<job>[A-Za-z0-9]+)(?:/(?P<slug>[^/?#]*))?")
_COMPANY_PATH = re.compile(r"^/companies/(?P<company>[^/?#]+)")


def to_ascii_digits(text: str) -> str:
    return text.translate(_DIGITS)


def clean_text(text: str | None) -> str:
    """Normalise whitespace while keeping paragraph breaks (ZWNJ is meaningful in Persian)."""
    if not text:
        return ""
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    joined = "\n".join(lines)
    return _MULTI_NL.sub("\n\n", joined).strip()


def one_line(text: str | None) -> str:
    return _WS.sub(" ", (text or "").replace("\n", " ")).strip()


def parse_int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", to_ascii_digits(text))
    return int(digits) if digits else None


def parse_days_ago(text: str | None) -> int | None:
    """Turn labels like '(۲۱ روز پیش)' or '(امروز)' into a number of days."""
    if not text:
        return None
    t = to_ascii_digits(text)
    if "امروز" in t:
        return 0
    if "دیروز" in t:
        return 1
    m = re.search(r"(\d+)\s*روز", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(ماه)", t)
    if m:
        return int(m.group(1)) * 30
    return None


def absolute_url(href: str | None, base: str = BASE_URL) -> str | None:
    if not href:
        return None
    return urljoin(base + "/", href)


def strip_tracking(url: str) -> str:
    """Remove Jobinja's `_ref` / `_t` tracking query params."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [p for p in parts.query.split("&") if p and not p.split("=", 1)[0] in {"_ref", "_t"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))


def parse_job_url(url: str) -> tuple[str, str, str | None]:
    """Return (company_slug, job_short_id, title_slug) for a Jobinja job URL."""
    path = urlsplit(url).path if "://" in url else url
    m = _JOB_PATH.match(path)
    if not m:
        raise ValueError(f"Not a Jobinja job URL: {url}")
    slug = unquote(m.group("slug")) if m.group("slug") else None
    return unquote(m.group("company")), m.group("job"), slug


def parse_company_slug(value: str) -> str:
    """Accept a company slug or any URL under /companies/<slug>."""
    value = value.strip()
    if "/" not in value:
        return value
    path = urlsplit(value).path if "://" in value else value
    m = _COMPANY_PATH.match(path)
    if not m:
        raise ValueError(f"Not a Jobinja company URL or slug: {value}")
    return unquote(m.group("company"))


def job_url(company_slug: str, job_id: str) -> str:
    return f"{BASE_URL}/companies/{quote(company_slug)}/jobs/{job_id}"


def company_url(company_slug: str, section: str = "") -> str:
    suffix = f"/{section}" if section else ""
    return f"{BASE_URL}/companies/{quote(company_slug)}{suffix}"


def split_bilingual_name(name: str) -> dict[str, str | None]:
    """'باتری باز | BatteryBuzz' -> {'name': ..., 'name_fa': 'باتری باز', 'name_en': 'BatteryBuzz'}."""
    name = one_line(name)
    if "|" in name:
        fa, en = (p.strip() for p in name.split("|", 1))
        return {"name": name, "name_fa": fa or None, "name_en": en or None}
    return {"name": name, "name_fa": name, "name_en": None}
