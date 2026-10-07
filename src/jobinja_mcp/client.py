from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import constants as C
from .browser_cookies import BrowserCookieError, find_jobinja_session
from .config import Settings
from .cookie_input import parse_cookie_text
from .http import AuthRequired, JobinjaError, JobinjaHTTP
from .parsing import (
    extract_init_states,
    parse_company_about,
    parse_company_categories,
    parse_company_jobs,
    parse_company_list,
    parse_company_overview,
    parse_job_cards,
    parse_job_detail,
    parse_job_search,
    parse_paginator,
    parse_top_companies,
    soup_of,
)
from .parsing.common import text_of
from .utils import company_url, job_url, parse_company_slug, parse_job_url
from .vocab import resolve, resolve_many

log = logging.getLogger(__name__)

META_TTL = 6 * 3600
STATIC_TTL = 24 * 3600


def _dig(data: Any, *paths: str) -> Any:
    """Return the first non-empty value found at any dotted path."""
    for path in paths:
        cur = data
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.lstrip("-").isdigit():
                idx = int(part)
                cur = cur[idx] if -len(cur) <= idx < len(cur) else None
            else:
                cur = None
            if cur is None:
                break
        if cur not in (None, "", [], {}):
            return cur
    return None


def _find_key(data: Any, key: str, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    return None


def summarize_application(app: dict[str, Any]) -> dict[str, Any]:
    statuses = app.get("statuses") or app.get("job_apply_statuses") or app.get("status_history")
    last_status = statuses[-1] if isinstance(statuses, list) and statuses else None
    status = _dig(app, "readable_status", "status", "state") or _dig(last_status or {}, "readable_status", "status")
    status_key = str(status) if status else ""
    return {
        "application_id": _dig(app, "short_id", "application_short_id", "id"),
        "job_title": _dig(app, "job.title", "job_title", "title"),
        "job_id": _dig(app, "job.short_id", "short_job_id", "job_short_id"),
        "company": _dig(app, "job.company.name", "company.name", "company_name"),
        "company_slug": _dig(app, "job.company.slug", "company.slug"),
        "status": status,
        "status_label": C.APPLICATION_STATUSES.get("dismissed" if status_key.startswith("dismissed") else status_key),
        "status_history": statuses if isinstance(statuses, list) else None,
        "applied_at": _dig(app, "created_at", "applied_at", "date", "jalali_created_at"),
        "updated_at": _dig(app, "updated_at", "status_updated_at"),
        "job_url": _dig(app, "job.url", "job_url", "url"),
    }


PASTE_INSTRUCTIONS = [
    "Open https://jobinja.ir in the browser where you're logged in.",
    "Press F12 to open DevTools and switch to the Network tab.",
    "Reload the page (F5).",
    "Right-click the first request in the list (jobinja.ir) and choose Copy > Copy as cURL (cmd or bash both work).",
    "Paste it here. It contains your login, so only paste it into this chat.",
]


class Jobinja:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.http = JobinjaHTTP(self.settings)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _account_api(self, method: str, path: str, **kwargs: Any) -> Any:
        return await self.http.api(method, path, require_auth=True, **kwargs)

    # ================================================================ vocab
    async def search_meta(self) -> dict[str, Any]:
        data = await self.http.api("GET", "/api/v10/job_search_meta", cache_ttl=META_TTL)
        return data.get("data", data)

    async def _job_categories_full(self) -> list[dict[str, Any]]:
        data = await self.http.api("GET", "/api/v10/job/categories", cache_ttl=STATIC_TTL)
        return data if isinstance(data, list) else data.get("data", [])

    async def _provinces_full(self) -> list[dict[str, Any]]:
        data = await self.http.api("GET", "/api/v10/region/province", params={"include_cities": 1}, cache_ttl=STATIC_TTL)
        return data.get("data", data) if isinstance(data, dict) else data

    async def _category_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for c in await self._job_categories_full():
            name = c.get("name")
            if not name:
                continue
            for alias in (name, c.get("english_name"), c.get("machine_name"), str(c.get("id"))):
                if alias:
                    aliases[alias] = name
        return aliases

    async def _province_aliases(self) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for p in await self._provinces_full():
            name = p.get("name")
            if not name:
                continue
            for alias in (name, (p.get("english_name") or "").strip(), p.get("slug"), str(p.get("id"))):
                if alias:
                    aliases[alias] = name
        return aliases

    async def _city_aliases(self) -> dict[str, str]:
        """Aliases -> 'Province-City' strings, the format used by job alerts."""
        aliases: dict[str, str] = {}
        for p in await self._provinces_full():
            for city in p.get("cities") or []:
                canonical = f"{p['name']}-{city['name']}"
                for alias in (canonical, city.get("name"), city.get("slug"), f"{p['name']} {city['name']}", f"{city['name']}، {p['name']}"):
                    if alias and alias not in aliases:
                        aliases[alias] = canonical
        return aliases

    async def search_options(self, include_cities: bool = False) -> dict[str, Any]:
        meta = await self.search_meta()
        english = {c.get("name"): c.get("english_name") for c in await self._job_categories_full()}
        prov_en = {p.get("name"): (p.get("english_name") or "").strip() for p in await self._provinces_full()}
        provinces = []
        for loc in meta.get("locations", []):
            item = {"name": loc["name"], "english_name": prov_en.get(loc["name"]), "job_count": loc.get("job_count")}
            if include_cities:
                item["cities"] = [{"name": c["name"], "job_count": c.get("job_count")} for c in loc.get("cities", [])]
            provinces.append(item)
        return {
            "sort_by": [{"value": k, "label": C.SORT_LABELS[k]} for k in C.SORT_OPTIONS],
            "job_categories": [
                {"name": c["name"], "english_name": english.get(c["name"]), "job_count": c.get("job_count")}
                for c in meta.get("job_categories", [])
            ],
            "provinces": provinces,
            "job_types": [{"value": k, "label": v["label"]} for k, v in C.JOB_TYPES.items()],
            "experience": [{"value": k, "label": v["label"]} for k, v in C.EXPERIENCE_LEVELS.items()],
            "min_salary_brackets_toman": C.SALARY_BRACKETS,
            "benefits": [{"value": k, "label": C.BENEFIT_LABELS[k]} for k in C.BENEFITS],
            "flags": {"remote": "دورکاری", "internship": "کارآموزی", "salary_negotiable": "توافقی"},
            "popular_skills": meta.get("skills", []),
            "company_categories": meta.get("company_categories", []),
            "company_sizes": meta.get("company_sizes", []),
        }

    # ================================================================= jobs
    async def build_search_params(
        self,
        keyword: str | None = None,
        categories: list[str] | None = None,
        provinces: list[str] | None = None,
        job_types: list[str] | None = None,
        experience: list[str] | None = None,
        min_salary_toman: int | None = None,
        salary_negotiable: bool = False,
        benefits: list[str] | None = None,
        remote: bool = False,
        internship: bool = False,
        sort_by: str = "relevance",
        page: int = 1,
    ) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = []
        if keyword:
            params.append(("filters[keywords][0]", keyword.strip()))
        if categories:
            for name in resolve_many(categories, await self._category_aliases(), "job category"):
                params.append(("filters[job_categories][]", name))
        if provinces:
            for name in resolve_many(provinces, await self._province_aliases(), "province"):
                params.append(("filters[locations][]", name))
        type_aliases = {**{k: v["value"] for k, v in C.JOB_TYPES.items()}, **{v["label"]: v["value"] for v in C.JOB_TYPES.values()}}
        for jt in resolve_many(job_types, type_aliases, "job type"):
            params.append(("filters[job_types][]", jt))
        exp_aliases = {**{k: v["value"] for k, v in C.EXPERIENCE_LEVELS.items()}, **{v["value"]: v["value"] for v in C.EXPERIENCE_LEVELS.values()}}
        for exp in resolve_many(experience, exp_aliases, "experience level"):
            params.append(("filters[w_e][]", exp))
        if min_salary_toman:
            params.append(("filters[sal_min][]", f"{int(min_salary_toman)}:"))
        if salary_negotiable:
            params.append(("filters[sal_min][]", ":"))
        benefit_aliases = {**C.BENEFITS, **{v: v for v in C.BENEFITS.values()}, **{C.BENEFIT_LABELS[k]: v for k, v in C.BENEFITS.items()}}
        for b in resolve_many(benefits, benefit_aliases, "benefit"):
            params.append((f"filters[{b}]", "1"))
        if remote:
            params.append(("filters[remote]", "1"))
        if internship:
            params.append(("filters[internship]", "1"))
        sort_aliases = {**C.SORT_OPTIONS, **{v: v for v in C.SORT_OPTIONS.values()}}
        params.append(("sort_by", resolve(sort_by or "relevance", sort_aliases, "sort option")))
        if page and page > 1:
            params.append(("page", str(int(page))))
        return params

    async def search_jobs(self, **filters: Any) -> dict[str, Any]:
        params = await self.build_search_params(**filters)
        html, final_url = await self.http.get_html("/jobs", params=params, cache_ttl=self.settings.cache_ttl)
        result = parse_job_search(html)
        result["search_url"] = final_url
        return result

    async def resolve_job(self, job: str, company: str | None = None) -> tuple[str, str]:
        """Accept a job URL, 'company/job_id', or a bare short id (+ optional company)."""
        job = job.strip()
        if "jobinja.ir" in job or job.startswith("/companies/"):
            c, j, _ = parse_job_url(job)
            return c, j
        if "/" in job:
            c, j = job.split("/", 1)
            return parse_company_slug(c), j.strip("/")
        if company:
            return parse_company_slug(company), job
        # Jobinja's Telegram short-link redirects a bare short id to the canonical job URL.
        resp = await self.http.request("GET", f"/tb-{job}")
        try:
            c, j, _ = parse_job_url(str(resp.url))
        except ValueError as exc:
            raise JobinjaError(f"Could not find job '{job}'. Pass the full job URL or the company slug too.") from exc
        return c, j

    async def get_job(self, job: str, company: str | None = None) -> dict[str, Any]:
        company_slug, job_id = await self.resolve_job(job, company)
        html, final_url = await self.http.get_html(job_url(company_slug, job_id), cache_ttl=self.settings.cache_ttl)
        return parse_job_detail(html, final_url)

    async def search_skills(self, query: str) -> list[dict[str, Any]]:
        data = await self.http.api("GET", "/api/v10/job_skills", params={"q": query}, cache_ttl=META_TTL)
        skills = _dig(data, "data.skills") or []
        if skills:
            return skills
        raw = await self.http.api("GET", "/api/v10/skill/search", params={"q": query}, cache_ttl=META_TTL)
        return [{"id": s.get("id"), "name": s.get("name"), "alias": s.get("alias")} for s in (raw or [])][:30]

    # ============================================================ companies
    async def search_companies(self, keyword: str | None = None, page: int = 1) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if keyword:
            params["keyword"] = keyword
        if page > 1:
            params["page"] = page
        html, _ = await self.http.get_html("/companies", params=params, cache_ttl=self.settings.cache_ttl)
        return parse_company_list(html)

    async def company_categories(self) -> list[dict[str, Any]]:
        html, _ = await self.http.get_html("/companies", cache_ttl=STATIC_TTL)
        return parse_company_categories(html)

    async def companies_by_category(self, category: str, page: int = 1) -> dict[str, Any]:
        # Only the sidebar industries are browsable; Jobinja silently returns the whole directory for any other slug.
        cats = await self.company_categories()
        aliases: dict[str, str] = {"other": "other", "others": "other"}
        for c in cats:
            if c["slug"]:
                aliases[c["name"]] = c["slug"]
                aliases[c["slug"]] = c["slug"]
        try:
            slug = resolve(category, aliases, "company category")
        except ValueError:
            names = ", ".join(c["name"] for c in cats)
            raise ValueError(
                f"'{category}' is not a browsable company category. Use one of: {names} "
                "(or 'other'), or call search_companies with a keyword instead."
            ) from None
        params: dict[str, Any] = {"page": page} if page > 1 else {}
        if slug == "other":
            html, _ = await self.http.get_html("/companies", params={"non_popular": 1, **params}, cache_ttl=self.settings.cache_ttl)
        else:
            html, _ = await self.http.get_html(f"/company/list/{quote(slug)}", params=params or None, cache_ttl=self.settings.cache_ttl)
        result = parse_company_list(html)
        result["category"] = next((c for c in cats if c["slug"] == slug), {"slug": slug})
        return result

    async def top_companies(self) -> list[dict[str, Any]]:
        html, _ = await self.http.get_html("/top50", cache_ttl=STATIC_TTL)
        return parse_top_companies(html)

    async def get_company(self, company: str) -> dict[str, Any]:
        slug = parse_company_slug(company)
        html, _ = await self.http.get_html(company_url(slug), cache_ttl=self.settings.cache_ttl)
        data = parse_company_overview(html, slug)
        data["url"] = company_url(slug)
        return data

    async def get_company_about(self, company: str) -> dict[str, Any]:
        slug = parse_company_slug(company)
        html, _ = await self.http.get_html(company_url(slug, "about"), cache_ttl=self.settings.cache_ttl)
        return parse_company_about(html, slug)

    async def get_company_jobs(self, company: str, page: int = 1, include_expired: bool = True) -> dict[str, Any]:
        slug = parse_company_slug(company)
        params = {"page": page} if page > 1 else None
        html, _ = await self.http.get_html(company_url(slug, "jobs"), params=params, cache_ttl=self.settings.cache_ttl)
        data = parse_company_jobs(html, slug)
        if not include_expired:
            data.pop("expired_jobs", None)
        return data

    # ================================================================= auth
    async def login(self, email: str | None = None, password: str | None = None) -> dict[str, Any]:
        email = email or self.settings.email
        password = password or self.settings.password
        if not email or not password:
            raise JobinjaError("Email and password are required (pass them or set JOBINJA_EMAIL / JOBINJA_PASSWORD).")
        html, _ = await self.http.get_html("/login/user")
        soup = soup_of(html)
        token_input = soup.select_one('form.c-loginForm input[name="_token"]')
        token = token_input.get("value") if token_input else self.http.csrf_token
        resp = await self.http.request(
            "POST",
            "/login/user",
            data={"_token": token, "redirect_url": "", "identifier": email, "password": password, "remember_me": "on"},
            headers={"Referer": f"{self.settings.base_url}/login/user", "Origin": self.settings.base_url},
        )
        self.http._absorb_html(resp.text)
        if self.http.authenticated and "/login/user" not in resp.url.path:
            self.http.save_session()
            return {"logged_in": True, "email": email, "landing_url": str(resp.url)}
        errors = [text_of(e) for e in soup_of(resp.text).select("form.c-loginForm .c-notify--error, form.c-loginForm .c-notify, .o-flyForm__error, .help-block")]
        errors = list(dict.fromkeys(e.split(" حساب کاربری ندارید")[0] for e in errors if e))
        raise JobinjaError("Login failed: " + ("; ".join(errors) if errors else "Jobinja rejected the credentials."), status=resp.status_code)

    async def browser_login(self, timeout_seconds: int = 300) -> dict[str, Any]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise JobinjaError(
                "Playwright is not installed. Run: pip install 'jobinja-mcp[browser]' && playwright install chromium"
            ) from exc
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.settings.browser_headless)
            context = await browser.new_context(user_agent=self.settings.user_agent, locale="fa-IR")
            page = await context.new_page()
            await page.goto(f"{self.settings.base_url}/login/user")
            deadline = timeout_seconds * 1000
            waited = 0
            authenticated = False
            while waited < deadline:
                try:
                    authenticated = bool(await page.evaluate("() => !!window.isAuthenticated"))
                except Exception:
                    authenticated = False
                if authenticated:
                    break
                await page.wait_for_timeout(1500)
                waited += 1500
            cookies = await context.cookies()
            await browser.close()
        if not authenticated:
            raise JobinjaError("Timed out waiting for you to log in inside the browser window.")
        imported = self.http.import_cookies(cookies)
        status = await self.session_status()
        self.http.save_session()
        return {"logged_in": status["logged_in"], "cookies_imported": imported}

    async def import_cookies(self, pasted: str) -> dict[str, Any]:
        cookies = [{**c, "domain": ".jobinja.ir"} for c in parse_cookie_text(pasted)]
        count = self.http.import_cookies(cookies, replace=True)
        status = await self.session_status()
        self.http.save_session()
        result = {"cookies_imported": count, **status}
        if not status["logged_in"]:
            result["hint"] = (
                "Those cookies aren't logged in. Make sure the copied request is from a jobinja.ir tab where you're "
                "signed in, and copy it again after reloading the page."
            )
        return result

    async def connect_account(self, cookies: str | None = None, try_browser: bool = True) -> dict[str, Any]:
        if cookies:
            return {"method": "pasted_cookies", **await self.import_cookies(cookies)}
        tried: list[str] = []
        had_saved = bool(self.http.cookie_names())
        status = await self.session_status()
        if status["logged_in"]:
            return {"method": "saved_session", **status}
        tried.append("saved session: not logged in" if had_saved else "saved session: none yet")

        if self.settings.email and self.settings.password:
            try:
                await self.login()
                return {"method": "password", **await self.session_status()}
            except JobinjaError as exc:
                tried.append(f"email/password from settings: {exc}")
        else:
            tried.append("email/password: not configured (JOBINJA_EMAIL / JOBINJA_PASSWORD)")

        if try_browser:
            try:
                result = await self.import_browser_session()
                if result["logged_in"]:
                    return {**result, "method": f"browser:{result['method']}"}
                tried.append(f"browser: {result.get('hint')}")
            except JobinjaError as exc:
                tried.append(f"browser: {exc}")

        return {
            "logged_in": False,
            "next_step": "ask_user_to_paste",
            "instructions_for_user": PASTE_INSTRUCTIONS,
            "then": "Call connect_account again with cookies set to exactly what the user pasted.",
            "tried": tried,
        }

    async def import_browser_session(self, browser: str = "auto", profile: str | None = None) -> dict[str, Any]:
        try:
            found = await find_jobinja_session(browser, profile)
        except BrowserCookieError as exc:
            raise JobinjaError(str(exc)) from exc
        count = self.http.import_cookies(found.cookies, replace=True)
        status = await self.session_status()
        self.http.save_session()
        result = {"browser": found.browser, "profile": found.profile, "method": found.method, "cookies_imported": count, **status}
        if not status["logged_in"]:
            result["hint"] = f"{found.browser} has jobinja.ir cookies but that session isn't logged in. Log in on jobinja.ir in the browser first."
        return result

    async def logout(self) -> dict[str, Any]:
        try:
            await self.http.request("GET", "/logout")
        finally:
            self.http.clear_session()
        return {"logged_in": False}

    async def session_status(self) -> dict[str, Any]:
        html, _ = await self.http.get_html("/")
        logged_in = bool(self.http.authenticated)
        info: dict[str, Any] = {"logged_in": logged_in}
        if logged_in:
            soup = soup_of(html)
            name = text_of(soup.select_one(".c-header__userName, .c-userMenu__name, .c-header__user span"))
            if name:
                info["display_name"] = name
            # Jobinja's "remember me" cookie keeps the login alive long after the daily session cookie expires.
            info["long_lived_login"] = any(n.startswith("remember_") for n in self.http.cookie_names())
            if not info["long_lived_login"]:
                info["note"] = "This login has no 'remember me' cookie, so it will likely end within a day."
            self.http.save_session()
        return info

    async def get_account(self) -> dict[str, Any]:
        html, _ = await self.http.get_html("/account", require_auth=True)
        soup = soup_of(html)
        fields: dict[str, Any] = {}
        for inp in soup.select("form input[name], form select[name], form textarea[name]"):
            name = inp.get("name")
            if not name or name.startswith("_") or "password" in name:
                continue
            if inp.name == "select":
                opt = inp.select_one("option[selected]")
                fields[name] = opt.get("value") if opt else None
            elif inp.get("type") in ("checkbox", "radio"):
                if inp.has_attr("checked"):
                    fields[name] = inp.get("value", True)
            else:
                fields[name] = inp.get("value") or inp.get_text(strip=True) or None
        states = extract_init_states(html)
        return {"fields": fields, "state": states[0] if states else None}

    # ===================================================== resume & uploads
    async def upload_document(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise JobinjaError(f"File not found: {path}")
        if path.suffix.lower() not in C.RESUME_EXTENSIONS | C.IMAGE_EXTENSIONS:
            raise JobinjaError(f"Unsupported file type {path.suffix}. Use PDF, DOC or DOCX for resumes.")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise JobinjaError("File is larger than 10 MB.")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        endpoint = "/api/v10/uploads/images" if path.suffix.lower() in C.IMAGE_EXTENSIONS else "/api/v10/uploads/documents"
        with path.open("rb") as fh:
            data = await self._account_api("POST", endpoint, files={"file": (path.name, fh, mime)})
        return _uploaded_file(data)

    async def save_resume_to_profile(
        self, file_id: str, full_name: str | None = None, email: str | None = None, phone: str | None = None
    ) -> Any:
        body = {"email": email, "full_name": full_name, "contact_number": phone, "file_id": file_id}
        return await self._account_api("POST", "/api/v10/end-users/cv-upload", json_body={k: v for k, v in body.items() if v is not None})

    async def get_resume(self) -> Any:
        return await self._account_api("GET", "/api/v10/resume")

    # ================================================================ apply
    async def get_apply_form(self, job: str, company: str | None = None) -> dict[str, Any]:
        company_slug, job_id = await self.resolve_job(job, company)
        url = f"{job_url(company_slug, job_id)}/apply"
        html, final_url = await self.http.get_html(url, require_auth=True)
        states = extract_init_states(html)
        state = next((s for s in states if any(k in s for k in ("form_name", "file", "needs_phone_verification", "email"))), states[0] if states else {})
        default_cv = state.get("file") if isinstance(state.get("file"), dict) else None
        result = {
            "company_slug": company_slug,
            "job_id": job_id,
            "apply_page": final_url,
            "already_applied": "/jobs/applied/" in final_url,
            "full_name": _dig(state, "full_name", "user.full_name", "fullName"),
            "email": _dig(state, "email", "user.email"),
            "phone": _dig(state, "contact_number", "telephone", "user.mobile", "employee.mobile"),
            "default_resume": (
                {"file_id": default_cv.get("id"), "name": default_cv.get("original_name"), "url": default_cv.get("url")} if default_cv else None
            ),
            "resume_outdated": state.get("is_out_dated"),
            "profile_cv_score": state.get("cvScore"),
            "minimum_required_cv_score": state.get("minimum_required_cv_score"),
            "needs_phone_verification": state.get("needs_phone_verification"),
            "suggest_job_alert": state.get("needs_job_alert"),
            "form_name": state.get("form_name"),
        }
        if not states:
            soup = soup_of(html)
            result["page_message"] = text_of(soup.select_one("#apply-form, .c-jobApply, main")) or None
        return result

    async def apply(
        self,
        job: str,
        company: str | None = None,
        *,
        resume_path: str | None = None,
        file_id: str | None = None,
        use_profile: bool = False,
        full_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        create_job_alert: bool = False,
        verification_code: str | None = None,
    ) -> dict[str, Any]:
        form = await self.get_apply_form(job, company)
        if form["already_applied"]:
            return {"status": "already_applied", "application_page": form["apply_page"]}

        uploaded = None
        if resume_path:
            uploaded = await self.upload_document(resume_path)
            file_id = str(uploaded["file_id"])
        if not use_profile and not file_id:
            default = form.get("default_resume") or {}
            file_id = str(default["file_id"]) if default.get("file_id") else None
        if not use_profile and not file_id:
            raise JobinjaError(
                "No resume to send. Provide resume_path, file_id, or set use_profile=true to send your Jobinja profile resume."
            )

        body = {
            "email": email or form.get("email"),
            "_ref": "",
            "full_name": full_name or form.get("full_name"),
            "contact_number": phone or form.get("phone"),
            "file_id": "" if use_profile else file_id,
            "create_job_alert": 1 if create_job_alert else 0,
            "verification_token": verification_code or "",
        }
        missing = [k for k in ("email", "full_name", "contact_number") if not body.get(k)]
        if missing:
            raise JobinjaError(f"Missing required applicant fields: {', '.join(missing)}")

        endpoint = f"/api/v10/companies/{quote(form['company_slug'])}/jobs/{form['job_id']}/apply"
        try:
            data = await self._account_api("POST", endpoint, json_body=body, referer=form["apply_page"])
        except JobinjaError as exc:
            error_type = _find_key(exc.payload, "error_type")
            if error_type == "SENT_VERIFICATION_TOKEN":
                return {
                    "status": "verification_required",
                    "message": "Jobinja sent an SMS verification code to the phone number. Call apply_to_job again with verification_code.",
                    "phone": body["contact_number"],
                    "file_id": file_id,
                }
            if error_type == "INVALID_VERIFICATION_TOKEN":
                return {"status": "invalid_verification_code", "message": "The SMS code was rejected. Check it and try again.", "file_id": file_id}
            errors = _find_key(exc.payload, "errors")
            return {"status": "failed", "message": str(exc), "errors": errors, "file_id": file_id}

        app_id = _dig(data, "data.apply.short_id", "data.data.apply.short_id", "apply.short_id")
        return {
            "status": "applied",
            "application_id": app_id,
            "application_url": f"{self.settings.base_url}/jobs/applied/{app_id}" if app_id else None,
            "uploaded_resume": uploaded,
            "response": data if not app_id else None,
        }

    # ========================================================= applications
    async def applied_jobs(self, status: str | None = None, page: int = 1, include_raw: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if page > 1:
            params["page"] = page
        html, _ = await self.http.get_html("/jobs/applied", params=params or None, require_auth=True)
        states = extract_init_states(html)
        state = next((s for s in states if "applications" in s), None)
        if state is None:
            try:
                api_data = await self._account_api("GET", "/api/v10/jobs/applied", params=params or None)
            except JobinjaError:
                api_data = None
            if api_data is not None:
                state = {"applications": api_data.get("data", api_data) if isinstance(api_data, dict) else api_data}
        if state is None:
            soup = soup_of(html)
            return {"applications": [], "note": "Could not find application data on the page.", "page_text": text_of(soup.select_one("main, .container"))[:2000]}

        apps_block = state.get("applications")
        items = apps_block.get("data", []) if isinstance(apps_block, dict) else (apps_block or [])
        result: dict[str, Any] = {
            "status_filters": state.get("statuses"),
            "available_dates": state.get("availableDates"),
            "count": len(items),
            "applications": [summarize_application(a) for a in items if isinstance(a, dict)],
        }
        if isinstance(apps_block, dict):
            result["pagination"] = {k: apps_block.get(k) for k in ("current_page", "last_page", "per_page", "total") if k in apps_block}
        if include_raw:
            result["raw"] = items
        return result

    async def get_application(self, application_id: str) -> dict[str, Any]:
        html, final_url = await self.http.get_html(f"/jobs/applied/{application_id}", require_auth=True)
        states = extract_init_states(html)
        merged: dict[str, Any] = {}
        for s in states:
            merged.update(s)
        job_apply = merged.get("job_apply") if isinstance(merged.get("job_apply"), dict) else None
        summary = summarize_application(job_apply) if job_apply else {}
        return {"application_id": application_id, "url": final_url, **summary, "details": merged or None}

    async def cancel_application(self, application_id: str, reason: str) -> Any:
        return await self._account_api(
            "POST", f"/api/v10/jobs/applied/{application_id}/canceled", json_body={"reason": reason},
            referer=f"{self.settings.base_url}/jobs/applied/{application_id}",
        )

    async def delete_application(self, application_id: str) -> Any:
        return await self._account_api("DELETE", f"/api/v10/jobseeker-app/applications/{application_id}")

    async def update_cover_letter(self, application_id: str, cover_letter: str, attachment_paths: list[str] | None = None) -> Any:
        file_ids: list[str] = []
        for p in attachment_paths or []:
            path = Path(p).expanduser()
            if not path.is_file():
                raise JobinjaError(f"File not found: {path}")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as fh:
                data = await self._account_api(
                    "POST", f"/api/v10/jobseeker-app/applications/{application_id}/cover-letter-upload", files={"file": (path.name, fh, mime)}
                )
            file_ids.append(str(_uploaded_file(data)["file_id"]))
        return await self._account_api(
            "PUT", f"/api/v10/jobseeker-app/applications/{application_id}/update-cover-letter",
            json_body={"cover_letter": cover_letter, "files": file_ids},
        )

    # ============================================================ bookmarks
    async def set_job_saved(self, job: str, saved: bool, company: str | None = None) -> dict[str, Any]:
        _, job_id = await self.resolve_job(job, company)
        method = "POST" if saved else "DELETE"
        data: Any = None
        try:
            data = await self.http.api(method, f"/api/v2/jobs/{job_id}/favorites")
        except JobinjaError as exc:
            if exc.status not in (404, 409, 422):
                raise
        user_type = _find_key(data, "user_type") if data else None
        result: dict[str, Any] = {"job_id": job_id, "saved": saved}
        if saved and (user_type == "guests" or (user_type is None and not self.http.authenticated)):
            result["note"] = "Saved as a guest bookmark on this server's browser session; log in to keep it on your account."
        self.http.save_session()
        return result

    async def _job_list_page(self, path: str, page: int) -> dict[str, Any]:
        html, _ = await self.http.get_html(path, params={"page": page} if page > 1 else None, require_auth=True)
        soup = soup_of(html)
        jobs = parse_job_cards(soup)
        return {**parse_paginator(soup), "count": len(jobs), "jobs": jobs}

    async def saved_jobs(self, page: int = 1) -> dict[str, Any]:
        return await self._job_list_page("/liked", page)

    async def recommended_jobs(self, page: int = 1) -> dict[str, Any]:
        return await self._job_list_page("/recommended", page)

    # =========================================================== job alerts
    async def job_alerts(self) -> dict[str, Any]:
        html, _ = await self.http.get_html("/job-alert", require_auth=True)
        soup = soup_of(html)
        alerts = []
        for el in soup.select(".js-jobAlert"):
            delete_btn = el.select_one(".js-jobAlertDelete")
            alert_id = delete_btn.get("data-job-alert-id") if delete_btn else None
            links = {text_of(a) or a["href"]: a["href"] for a in el.select("a[href]")}
            alerts.append({"alert_id": alert_id, "title": delete_btn.get("data-job-alert-title") if delete_btn else None, "summary": text_of(el), "links": links})
        return {"count": len(alerts), "alerts": alerts, "state": extract_init_states(html) or None}

    async def _alert_body(
        self, keywords: str, categories: list[str] | None, cities: list[str] | None, job_type: str | None, frequency: str, email: str | None
    ) -> dict[str, Any]:
        job_type_values = {"any": None, "full_time": "full_time", "part_time": "part_time", "internship": "internship", "remote": "remote"}
        if job_type not in (None, *job_type_values):
            raise JobinjaError(f"job_type must be one of {list(job_type_values)}")
        body: dict[str, Any] = {
            "keywords": keywords,
            "period_in_days": 7 if frequency == "weekly" else 1,
            "job_type": job_type_values.get(job_type or "any"),
            "provinces": resolve_many(cities, await self._city_aliases(), "city") if cities else [],
            "job_categories": resolve_many(categories, await self._category_aliases(), "job category") if categories else [],
            "alert_from": "manual",
        }
        if email:
            body["email"] = email
        return body

    async def create_job_alert(self, keywords: str, categories: list[str] | None = None, cities: list[str] | None = None,
                               job_type: str | None = None, frequency: str = "daily", email: str | None = None) -> Any:
        body = await self._alert_body(keywords, categories, cities, job_type, frequency, email)
        return await self._account_api("POST", "/api/v10/job-alert", json_body=body, referer=f"{self.settings.base_url}/job-alert/create")

    async def update_job_alert(self, alert_id: str, keywords: str, categories: list[str] | None = None, cities: list[str] | None = None,
                               job_type: str | None = None, frequency: str = "daily", email: str | None = None) -> Any:
        body = await self._alert_body(keywords, categories, cities, job_type, frequency, email)
        return await self._account_api("PUT", f"/api/v10/job-alert/{alert_id}", json_body=body)

    async def delete_job_alert(self, alert_id: str) -> Any:
        return await self._account_api("DELETE", f"/api/v10/job-alert/{alert_id}")

    async def job_alert_results(self, alert_id: str, page: int = 1) -> dict[str, Any]:
        return await self._job_list_page(f"/job-alert/{alert_id}/result", page)

    # ========================================================== misc account
    async def notifications(self) -> Any:
        return await self._account_api("GET", "/api/v10/notifications/employee")

    async def mark_notifications_seen(self) -> Any:
        return await self._account_api("POST", "/api/v10/notifications/employee/seen")

    async def report_reasons(self) -> list[str]:
        data = await self.http.api("GET", "/api/v10/violation-report/all-reasons", cache_ttl=STATIC_TTL)
        return data if isinstance(data, list) else data.get("data", [])

    async def report_job(self, job: str, reason: str, details: str = "", company: str | None = None) -> Any:
        _, job_id = await self.resolve_job(job, company)
        return await self._account_api("POST", f"/api/v10/violation-report/job/{job_id}", json_body={"reasons": [reason], "text": details})


def _uploaded_file(data: Any) -> dict[str, Any]:
    body = data.get("data", data) if isinstance(data, dict) else {}
    main_key = body.get("mainKey") if isinstance(body, dict) else None
    info = body.get(main_key) if main_key else None
    if not isinstance(info, dict):
        info = next((v for v in body.values() if isinstance(v, dict) and "id" in v), body) if isinstance(body, dict) else {}
    file_id = info.get("id")
    if file_id is None:
        raise JobinjaError("Upload succeeded but no file id was returned", payload=data)
    return {"file_id": file_id, "name": info.get("original_name"), "url": info.get("url")}
