from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..utils import absolute_url, job_url, one_line, parse_days_ago, parse_job_url, split_bilingual_name, strip_tracking
from .common import attr, extract_json_ld, number_from, parse_paginator, rich_text, soup_of, text_of
from .companies import parse_company_header

_INFO_KEYS = {
    "دسته‌بندی شغلی": "category",
    "موقعیت مکانی": "location",
    "نوع همکاری": "employment_type",
    "حداقل سابقه کار": "min_experience",
    "حقوق": "salary",
    "مهارت‌های مورد نیاز": "skills",
    "جنسیت": "gender",
    "وضعیت نظام وظیفه": "military_status",
    "حداقل مدرک تحصیلی": "min_education",
    "زبان‌های مورد نیاز": "languages",
    "مزایا و تسهیلات": "benefits",
}
_LIST_KEYS = {"skills", "military_status", "languages", "benefits", "category"}


def parse_job_card(li: Tag) -> dict[str, Any] | None:
    link = li.select_one("a.c-jobListView__titleLink")
    if link is None:
        return None
    url = strip_tracking(absolute_url(link.get("href")) or "")
    try:
        company_slug, job_id, title_slug = parse_job_url(url)
    except ValueError:
        company_slug, job_id, title_slug = None, None, None

    passed = text_of(li.select_one(".c-jobListView__passedDays"))
    card: dict[str, Any] = {
        "job_id": job_id,
        "title": text_of(link),
        "url": url,
        "company_slug": company_slug,
        "company": None,
        "location": None,
        "contract_type": None,
        "salary": None,
        "posted_days_ago": parse_days_ago(passed),
        "posted_label": passed.strip("() ") or None,
        "expired": "منقضی" in passed,
        "premium": "c-jobListView__item--premium" in (attr(li, "class") or ""),
        "logo": attr(li.select_one("img.o-listView__itemIndicatorImage"), "src"),
    }
    for item in li.select("li.c-jobListView__metaItem"):
        icon = attr(item.select_one("i"), "class") or ""
        if "c-icon--construction" in icon:
            card["company"] = text_of(item)
        elif "c-icon--place" in icon:
            card["location"] = text_of(item)
        elif "c-icon--resume" in icon:
            spans = item.select("span > span")
            contract = text_of(spans[0]) if spans else ""
            card["contract_type"] = contract or None
            full = text_of(item)
            rest = one_line(full.replace(contract, "", 1)).strip("() ")
            if "وارد شوید" in rest:
                card["salary_hidden"] = True
            elif rest:
                card["salary"] = rest
    fave = li.select_one("fave-btn")
    if fave is not None:
        card["saved"] = attr(fave, "faved") == "true"
    controls = text_of(li.select_one(".c-jobListView__itemControls"))
    if "ارسال شده" in controls:
        card["applied"] = True
    return card


def parse_job_cards(root: BeautifulSoup | Tag) -> list[dict[str, Any]]:
    cards = []
    for li in root.select("li.c-jobListView__item"):
        card = parse_job_card(li)
        if card:
            cards.append(card)
    return cards


def parse_job_search(html: str) -> dict[str, Any]:
    soup = soup_of(html)
    total = number_from(text_of(soup.select_one(".c-jobSearchState__numberOfResultsEcho")))
    listing = soup.select_one(".c-jobSearchView") or soup
    jobs = parse_job_cards(listing)
    return {"total_results": total, **parse_paginator(soup), "count": len(jobs), "jobs": jobs}


def _info_items(root: Tag) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in root.select("li.c-infoBox__item"):
        title = text_of(item.select_one(".c-infoBox__itemTitle"))
        values = [one_line(text_of(s)).replace(" ،", "،") for s in item.select(".tags span")]
        values = [v for v in values if v]
        if not values:
            values = [text_of(item).replace(title, "", 1).strip()]
        key = _INFO_KEYS.get(title, title)
        out[key] = values if key in _LIST_KEYS or len(values) > 1 else values[0]
    return out


def _section_after(soup: BeautifulSoup, heading: str) -> Tag | None:
    for h in soup.select("h4.o-box__title"):
        if heading in text_of(h):
            nxt = h.find_next_sibling()
            if nxt is not None and "o-box__text" in (attr(nxt, "class") or ""):
                return nxt
    return None


def parse_job_detail(html: str, url: str) -> dict[str, Any]:
    soup = soup_of(html)
    company_slug, job_id, _ = parse_job_url(url)
    view = soup.select_one("section.c-jobView")
    if view is None:
        raise ValueError("Job page layout not recognised (no .c-jobView section)")

    title = text_of(view.select_one(".c-jobView__titleText h1"))
    info: dict[str, Any] = {}
    for box in view.select("ul.c-infoBox"):
        info.update(_info_items(box))

    desc_node = _section_after(soup, "شرح موقعیت شغلی")
    if desc_node is not None:
        for junk in desc_node.select("a.no-desc-display"):
            junk.decompose()
    about_node = _section_after(soup, "معرفی شرکت")

    # Jobinja's JSON-LD carries placeholder salary/remote values, so only dates and ids are taken from it.
    ld = extract_json_ld(soup, "JobPosting") or {}
    org = ld.get("hiringOrganization") if isinstance(ld.get("hiringOrganization"), dict) else {}
    canonical = job_url(company_slug, job_id)
    work_mode = " ".join(str(v) for v in (title, info.get("employment_type"), info.get("location")) if v)

    fave = view.select_one("fave-btn")
    apply_box = soup.select_one("#apply-form")
    apply_text = text_of(apply_box.select_one(".c-boxWidget")) if apply_box else ""
    page_text = text_of(view)

    similar_root = soup.select_one("#similar-jobs")
    detail: dict[str, Any] = {
        "job_id": job_id,
        "numeric_id": (ld.get("identifier") or {}).get("value") if isinstance(ld.get("identifier"), dict) else None,
        "url": canonical,
        "title": re.sub(r"^استخدام\s+", "", title).strip() or ld.get("title"),
        "full_title": title,
        "company": {
            **parse_company_header(soup),
            "slug": company_slug,
            "website": org.get("sameAs"),
        },
        **info,
        "date_posted": ld.get("datePosted"),
        "valid_through": ld.get("validThrough"),
        "employment_type_code": ld.get("employmentType"),
        "remote": "دورکاری" in work_mode or "remote" in work_mode.lower(),
        "internship": "کارآموزی" in work_mode,
        "description": rich_text(desc_node) if desc_node is not None else None,
        "company_intro": rich_text(about_node) if about_node is not None else None,
        "saved": (attr(fave, "faved") == "true") if fave is not None else None,
        "expired": "منقضی" in page_text or "این آگهی منقضی شده" in html,
        "apply_url": f"{canonical}/apply",
        "apply_box": apply_text or None,
        "already_applied": any(s in apply_text for s in ("قبلا", "ارسال شده", "مشاهده وضعیت")),
        "similar_jobs": parse_job_cards(similar_root) if similar_root is not None else [],
    }
    header_name = detail["company"].get("name")
    if not header_name and org.get("name"):
        detail["company"].update(split_bilingual_name(org["name"]))
    return detail
