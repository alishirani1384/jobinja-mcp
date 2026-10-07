from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from ..utils import absolute_url, parse_company_slug, split_bilingual_name
from .common import attr, number_from, parse_paginator, rich_text, soup_of, text_of

PROVINCES = {
    "تهران", "خراسان رضوی", "اصفهان", "البرز", "فارس", "مازندران", "قم", "آذربایجان شرقی", "گیلان",
    "خوزستان", "یزد", "کرمان", "قزوین", "هرمزگان", "گلستان", "بوشهر", "سیستان و بلوچستان",
    "آذربایجان غربی", "سمنان", "مرکزی", "اردبیل", "همدان", "زنجان", "کرمانشاه", "کردستان",
    "خراسان جنوبی", "لرستان", "ایلام", "خراسان شمالی", "چهارمحال بختیاری", "کهکیلویه و بویراحمد",
}
_BG_URL = re.compile(r"url\(\s*(['\"])(.+?)\1\s*\)|url\(\s*([^'\"\s)]+)\s*\)")


def _is_location(text: str) -> bool:
    return text.split("،")[0].strip() in PROVINCES


def _category_slug(href: str | None) -> str | None:
    if not href or "/company/list/" not in href:
        return None
    return unquote(urlsplit(href).path.rsplit("/company/list/", 1)[-1]) or None


# --------------------------------------------------------------------- listing
def parse_company_list(html: str) -> dict[str, Any]:
    soup = soup_of(html)
    companies = []
    for a in soup.select("a.c-companyOverview"):
        href = absolute_url(a.get("href"))
        tags = a.select(".c-companyOverview__tags span")
        hiring = any("green" in (attr(s, "class") or "") for s in tags)
        plain = [text_of(s) for s in tags if "green" not in (attr(s, "class") or "")]
        # Tags are positional: [location, category]; the location span is present but empty when unknown.
        if len(plain) >= 2:
            location, category = plain[0] or None, plain[1] or None
        elif plain and _is_location(plain[0]):
            location, category = plain[0], None
        else:
            location, category = None, (plain[0] if plain else None) or None
        companies.append(
            {
                "slug": parse_company_slug(href) if href else None,
                **split_bilingual_name(text_of(a.select_one(".c-companyOverview__title"))),
                "url": href,
                "location": location,
                "category": category,
                "hiring": hiring,
                "logo": attr(a.select_one(".c-companyOverview__logoImage"), "src"),
                "banner": attr(a.select_one(".c-companyOverview__bannerImage"), "src"),
            }
        )
    return {**parse_paginator(soup), "count": len(companies), "companies": companies}


def parse_company_categories(html: str) -> list[dict[str, Any]]:
    soup = soup_of(html)
    out = []
    for li in soup.select(".sidebar ul.menu li"):
        a = li.select_one("a[href]")
        if a is None:
            continue
        href = absolute_url(a["href"])
        out.append(
            {
                "name": text_of(a),
                "slug": _category_slug(href) or ("other" if "non_popular" in href else None),
                "url": href,
                "company_count": number_from(text_of(li.select_one(".indicator"))),
            }
        )
    return out


def parse_top_companies(html: str) -> list[dict[str, Any]]:
    soup = soup_of(html)
    out = []
    for li in soup.select("li.c-topCompanies__each"):
        page = li.select_one("a.c-topCompanies__goToCompany")
        href = absolute_url(page.get("href")) if page else None
        metas = [text_of(m) for m in li.select(".c-topCompanies__metasItem")]
        open_jobs = None
        meta_rest = []
        for m in metas:
            if "شغل فعال" in m:
                open_jobs = number_from(m)
            else:
                meta_rest.append(m)
        scores = {}
        for p in li.select(".c-topCompanies__parameter"):
            value = text_of(p.select_one(".c-topCompanies__parameterValue"))
            label = text_of(p).replace(value, "", 1).strip()
            scores[label] = number_from(value)
        rate_text = text_of(li.select_one(".c-topCompanies__rate"))
        rate = None
        m = re.search(r"([\d۰-۹]+)\s*/\s*([\d۰-۹]+)", rate_text)
        if m:
            rate = float(f"{number_from(m.group(1))}.{number_from(m.group(2))}")
        out.append(
            {
                "rank": number_from(text_of(li.select_one(".c-topCompanies__counter"))),
                **split_bilingual_name(text_of(li.select_one(".c-topCompanies__name"))),
                "slug": parse_company_slug(href) if href else None,
                "url": href,
                "rating": rate,
                "category": meta_rest[0] if meta_rest else None,
                "size": meta_rest[1] if len(meta_rest) > 1 else None,
                "open_jobs": open_jobs,
                "scores": scores,
                "logo": attr(li.select_one("img.c-topCompanies__logoImg"), "src"),
                "workspace_images": [attr(i, "src") or attr(i, "data-src") for i in li.select("img.c-topCompanies__workSpaceImage")],
            }
        )
    return out


# ---------------------------------------------------------------- company page
def parse_company_header(soup: BeautifulSoup) -> dict[str, Any]:
    header = soup.select_one(".c-companyHeader")
    if header is None:
        return {}
    info: dict[str, Any] = split_bilingual_name(text_of(header.select_one(".c-companyHeader__name")))
    info["logo"] = attr(header.select_one(".c-companyHeader__logoImage"), "src")
    cover = header.select_one(".c-companyHeader__cover")
    m = _BG_URL.search(attr(cover, "style") or "") if cover else None
    info["cover_image"] = (m.group(2) or m.group(3)) if m else None
    info["premium"] = "c-companyHeader--premium" in (attr(header, "class") or "")
    info["category"] = info["category_slug"] = info["size"] = info["website"] = None
    for item in header.select(".c-companyHeader__metaItem"):
        link = item.select_one("a[href]")
        value = text_of(item)
        if link is not None and "/company/list/" in link["href"]:
            info["category"] = value
            info["category_slug"] = _category_slug(link["href"])
        elif link is not None:
            info["website"] = link["href"]
        elif value:
            info["size"] = value
    jobs = header.select_one(".c-companyHeader__navigatorOpenJobs")
    info["open_jobs_count"] = number_from(text_of(jobs)) if jobs else 0
    return info


def parse_company_overview(html: str, slug: str) -> dict[str, Any]:
    soup = soup_of(html)
    data = {"slug": slug, **parse_company_header(soup)}
    data["tagline"] = text_of(soup.select_one(".c-featurePhoto__title")) or None
    data["feature_image"] = attr(soup.select_one(".c-featurePhoto__img"), "src")
    data["location"] = text_of(soup.select_one(".c-companyMap__desc")) or None
    map_link = attr(soup.select_one("a.c-companyMap__mapLink"), "href") or ""
    m = re.search(r"q=(-?[\d.]+),(-?[\d.]+)", map_link)
    data["coordinates"] = {"lat": float(m.group(1)), "lng": float(m.group(2))} if m else None
    data["members"] = [
        {
            "name": text_of(q.select_one(".c-companyQuote__title")).replace(text_of(q.select_one(".c-companyQuote__role")), "").strip(),
            "role": text_of(q.select_one(".c-companyQuote__role")) or None,
            "quote": text_of(q.select_one(".c-companyQuote__desc")) or None,
            "avatar": attr(q.select_one("img.c-companyQuote__avatar"), "src"),
        }
        for q in soup.select(".c-companyMembers .c-companyQuote")
    ]
    data["highlights"] = [text_of(h) for h in soup.select(".c-featurePhoto__title")][1:]
    return data


def parse_company_about(html: str, slug: str) -> dict[str, Any]:
    soup = soup_of(html)
    sections = [
        {"title": text_of(s.select_one(".c-cardText__title")), "body": rich_text(s.select_one(".c-cardText__body"))}
        for s in soup.select("section.c-cardText")
        if s.select_one(".c-cardText__body") is not None
    ]
    photos = [
        {"src": attr(f.select_one("img"), "src"), "caption": text_of(f.select_one(".c-featurePhoto__title")) or attr(f.select_one("img"), "alt")}
        for f in soup.select("figure.c-featurePhoto")
        if f.select_one("img") is not None
    ]
    return {"slug": slug, **parse_company_header(soup), "sections": sections, "photos": photos}


def parse_company_jobs(html: str, slug: str) -> dict[str, Any]:
    from .jobs import parse_job_cards

    soup = soup_of(html)
    intro = soup.select_one("section.c-cardText")
    all_cards = parse_job_cards(soup)
    open_jobs = [c for c in all_cards if not c["expired"]]
    expired_jobs = [c for c in all_cards if c["expired"]]
    return {
        "slug": slug,
        **parse_company_header(soup),
        "intro": rich_text(intro.select_one(".c-cardText__body")) if intro and intro.select_one(".c-cardText__body") else None,
        "open_jobs_count": len(open_jobs),
        "open_jobs": open_jobs,
        "expired_jobs": expired_jobs,
        **parse_paginator(soup),
    }
