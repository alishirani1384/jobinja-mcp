from __future__ import annotations

import functools
import json
import logging
from typing import Annotated, Any, Awaitable, Callable, Literal, TypeVar

from pydantic import Field

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ToolAnnotations

from .client import Jobinja
from .config import Settings
from .http import AuthRequired, JobinjaError

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Tools for Jobinja.ir (جابینجا), Iran's largest job board.

Typical flow:
1. `get_search_options` to learn valid categories, provinces, benefits and sort orders
   (Persian or English names are both accepted by every tool).
2. `search_jobs` -> `get_job` for full details -> `get_company` / `get_company_about` for the employer.
3. Account features need a session. Call `connect_account` first: it reuses a saved session or logs in on
   its own when it can, otherwise it returns steps to show the user; pass what they paste back to it.
   `session_status` checks the current state.
4. Applying: `get_apply_form` shows what will be sent; `apply_to_job` submits.
   Always confirm the job and the resume with the user before calling `apply_to_job`.
   If it returns status=verification_required, ask the user for the SMS code and call it again
   with `verification_code`.
5. Track applications with `list_applied_jobs` and `get_application`.

Salaries are in Toman. Search results are 20 per page.
"""

T = TypeVar("T")

_settings = Settings.from_env()
_client: Jobinja | None = None


def client() -> Jobinja:
    global _client
    if _client is None:
        _client = Jobinja(_settings)
    return _client


def _tool_errors(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await fn(*args, **kwargs)
        except AuthRequired as exc:
            raise ToolError(str(exc)) from exc
        except JobinjaError as exc:
            detail = f" Details: {json.dumps(exc.payload, ensure_ascii=False)[:800]}" if exc.payload else ""
            raise ToolError(f"{exc}{detail}") from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

mcp = MCPServer("jobinja", instructions=INSTRUCTIONS)


def tool(annotations: ToolAnnotations, title: str):
    def decorator(fn: Callable[..., Awaitable[Any]]):
        return mcp.tool(annotations=annotations, title=title)(_tool_errors(fn))

    return decorator


JobRef = Annotated[
    str,
    Field(description="Job URL (https://jobinja.ir/companies/<company>/jobs/<id>/...), '<company>/<id>', or the short job id like 'tEk6'."),
]
CompanyRef = Annotated[str, Field(description="Company slug (e.g. 'batterybuzz') or any jobinja.ir/companies/<slug> URL.")]
Page = Annotated[int, Field(ge=1, description="1-based page number.")]

# ============================================================== discovery
@tool(READ, "Search options")
async def get_search_options(
    include_cities: Annotated[bool, Field(description="Also list cities inside each province.")] = False,
) -> dict[str, Any]:
    """List every valid value for job search filters: job categories, provinces (and cities), job types,
    experience levels, salary brackets, benefits/perks, sort orders, popular skills, company categories and sizes,
    each with live job counts."""
    return await client().search_options(include_cities=include_cities)


@tool(READ, "Search jobs")
async def search_jobs(
    keyword: Annotated[str | None, Field(description="Free text: job title, skill or company, e.g. 'React', 'حسابدار'.")] = None,
    categories: Annotated[list[str] | None, Field(description="Job categories (Persian or English), e.g. ['web, software development'].")] = None,
    provinces: Annotated[list[str] | None, Field(description="Provinces, e.g. ['تهران', 'Isfahan'].")] = None,
    job_types: Annotated[list[Literal["full_time", "part_time"]] | None, Field(description="Contract types.")] = None,
    experience: Annotated[list[Literal["any", "under_3", "3_to_6", "over_6"]] | None, Field(description="Required work experience (years).")] = None,
    min_salary_toman: Annotated[int | None, Field(ge=0, description="Minimum monthly salary in Toman, e.g. 40000000.")] = None,
    salary_negotiable: Annotated[bool, Field(description="Include jobs with negotiable (توافقی) salary.")] = False,
    benefits: Annotated[list[str] | None, Field(description="Perks, e.g. ['flexible_hours','supplementary_insurance','military_service_placement','usd_payment','loan','bonus','esop']. See get_search_options.")] = None,
    remote: Annotated[bool, Field(description="Only remote (دورکاری) jobs.")] = False,
    internship: Annotated[bool, Field(description="Only internships (کارآموزی).")] = False,
    sort_by: Annotated[Literal["relevance", "newest", "highest_salary"], Field(description="Sort order.")] = "relevance",
    page: Page = 1,
) -> dict[str, Any]:
    """Search Jobinja job ads with the same filters as https://jobinja.ir/jobs. Returns total result count,
    pagination and job cards (title, company, location, contract, salary when visible, posted days ago, url)."""
    return await client().search_jobs(
        keyword=keyword, categories=categories, provinces=provinces, job_types=job_types, experience=experience,
        min_salary_toman=min_salary_toman, salary_negotiable=salary_negotiable, benefits=benefits,
        remote=remote, internship=internship, sort_by=sort_by, page=page,
    )


@tool(READ, "Job details")
async def get_job(
    job: JobRef,
    company: Annotated[str | None, Field(description="Company slug, if `job` is a bare short id.")] = None,
) -> dict[str, Any]:
    """Full details of one job ad: description, requirements, category, location, contract type, experience,
    salary, skills, gender, military-service and education requirements, posting date, remote flag,
    company summary, similar jobs and whether you've saved/applied."""
    return await client().get_job(job, company)


@tool(READ, "Search skills")
async def search_skills(query: Annotated[str, Field(description="Partial skill name, e.g. 'react'.")]) -> list[dict[str, Any]]:
    """Autocomplete skill names as Jobinja knows them (with job counts when available)."""
    return await client().search_skills(query)


# ============================================================== companies
@tool(READ, "Search companies")
async def search_companies(
    keyword: Annotated[str | None, Field(description="Company name or keyword, e.g. 'دیجی‌کالا', 'snapp'. Omit to browse all.")] = None,
    page: Page = 1,
) -> dict[str, Any]:
    """Search or browse the company directory at https://jobinja.ir/companies."""
    return await client().search_companies(keyword, page)


@tool(READ, "Company categories")
async def list_company_categories() -> list[dict[str, Any]]:
    """All company industry categories with the number of companies in each."""
    return await client().company_categories()


@tool(READ, "Companies by category")
async def browse_companies_by_category(
    category: Annotated[str, Field(description="Category name or slug from list_company_categories.")],
    page: Page = 1,
) -> dict[str, Any]:
    """List companies in one industry category."""
    return await client().companies_by_category(category, page)


@tool(READ, "Top 50 companies")
async def get_top_companies() -> list[dict[str, Any]]:
    """Jobinja's ranking of the 50 best companies to work for, with rating, scores, size and open jobs."""
    return await client().top_companies()


@tool(READ, "Company profile")
async def get_company(company: CompanyRef) -> dict[str, Any]:
    """Company overview page: names, logo, industry, size, website, location & map coordinates,
    tagline, team member quotes and number of open jobs."""
    return await client().get_company(company)


@tool(READ, "Company about page")
async def get_company_about(company: CompanyRef) -> dict[str, Any]:
    """The company's 'معرفی' page: introduction, work culture, benefits sections and photos."""
    return await client().get_company_about(company)


@tool(READ, "Company jobs")
async def get_company_jobs(
    company: CompanyRef,
    include_expired: Annotated[bool, Field(description="Also return recently expired ads.")] = True,
    page: Page = 1,
) -> dict[str, Any]:
    """All open (and optionally expired) job ads of a company."""
    return await client().get_company_jobs(company, page, include_expired)


# =================================================================== auth
@tool(WRITE_IDEMPOTENT, "Log in")
async def login(
    email: Annotated[str | None, Field(description="Jobinja account email. Defaults to JOBINJA_EMAIL.")] = None,
    password: Annotated[str | None, Field(description="Password. Defaults to JOBINJA_PASSWORD.")] = None,
) -> dict[str, Any]:
    """Log in with email and password. The session is saved to disk and reused across restarts."""
    return await client().login(email, password)


@tool(WRITE_IDEMPOTENT, "Log in with a browser")
async def login_with_browser(
    timeout_seconds: Annotated[int, Field(ge=30, le=900, description="How long to wait for you to finish logging in.")] = 300,
) -> dict[str, Any]:
    """Open a real Chromium window (Playwright) so the user can log in any way Jobinja supports
    (Google, LinkedIn, captcha). The session cookies are then captured and saved."""
    return await client().browser_login(timeout_seconds)


@tool(WRITE_IDEMPOTENT, "Import browser cookies")
async def import_cookies(
    pasted: Annotated[
        str,
        Field(description="Exactly what the user copied from a logged-in jobinja.ir tab: DevTools 'Copy as cURL' (cmd or bash), "
              "'Copy as PowerShell', the `cookie` request header, or a JSON cookie export."),
    ],
) -> dict[str, Any]:
    """Log in with cookies the user copied from their browser. Prefer `connect_account`, which calls this for you."""
    return await client().import_cookies(pasted)


@tool(WRITE_IDEMPOTENT, "Connect Jobinja account")
async def connect_account(
    cookies: Annotated[
        str | None,
        Field(description="Only on the second call: exactly what the user pasted after following `instructions_for_user`."),
    ] = None,
    try_browser: Annotated[bool, Field(description="Try reading the login from a local browser before asking the user.")] = True,
) -> dict[str, Any]:
    """Get logged in to Jobinja. Call this before any account tool when not logged in.

    Tries, in order: the saved session, JOBINJA_EMAIL/JOBINJA_PASSWORD, and the user's browser. If it returns
    next_step=ask_user_to_paste, show the user `instructions_for_user` as a numbered list, wait for them to paste,
    then call this again with `cookies` set to their paste, unchanged. The login normally lasts about a year."""
    return await client().connect_account(cookies, try_browser)


@tool(WRITE_IDEMPOTENT, "Use my browser's Jobinja login")
async def import_browser_session(
    browser: Annotated[
        str, Field(description="'auto', 'chrome', 'edge', 'brave', 'vivaldi', 'opera', 'chromium' or 'firefox'.")
    ] = "auto",
    profile: Annotated[str | None, Field(description="Browser profile folder, e.g. 'Default' or 'Profile 2'. Defaults to whichever is logged in.")] = None,
) -> dict[str, Any]:
    """Log in by reusing the jobinja.ir session of a browser on this machine where the user is already logged in.
    No password needed. Only jobinja.ir cookies are read.

    Current Chrome, Edge and Brave use App-Bound cookie encryption that only the running browser can unlock: the
    user must enable chrome://inspect/#remote-debugging once, keep the browser open and click "Allow" when prompted.
    If they don't want that, use `import_cookies` instead (DevTools > Network > reload > first request > copy the
    `cookie` request header). Firefox works directly.
    The browser and this server then share one session, so `logout` here also logs the browser out."""
    return await client().import_browser_session(browser, profile)


@tool(READ, "Session status")
async def session_status() -> dict[str, Any]:
    """Check whether the server is logged in to Jobinja."""
    return await client().session_status()


@tool(WRITE_IDEMPOTENT, "Log out")
async def logout() -> dict[str, Any]:
    """Log out and delete the saved session."""
    return await client().logout()


@tool(READ, "Account settings")
async def get_account() -> dict[str, Any]:
    """Your account settings (name, email, phone, notification preferences)."""
    return await client().get_account()


# ========================================================= resume / apply
@tool(WRITE, "Upload resume file")
async def upload_resume(
    file_path: Annotated[str, Field(description="Absolute path to a PDF/DOC/DOCX resume on this machine (max 10 MB).")],
    save_to_profile: Annotated[bool, Field(description="Also set it as the resume on your Jobinja profile.")] = False,
    full_name: Annotated[str | None, Field(description="Needed when save_to_profile is true.")] = None,
    email: Annotated[str | None, Field(description="Needed when save_to_profile is true.")] = None,
    phone: Annotated[str | None, Field(description="Mobile number, needed when save_to_profile is true.")] = None,
) -> dict[str, Any]:
    """Upload a resume file to Jobinja and get its file_id (usable with apply_to_job)."""
    c = client()
    uploaded = await c.upload_document(file_path)
    if save_to_profile:
        uploaded["profile_update"] = await c.save_resume_to_profile(str(uploaded["file_id"]), full_name, email, phone)
    return uploaded


@tool(READ, "My Jobinja resume")
async def get_my_resume() -> Any:
    """Your Jobinja profile resume (CV builder data: personal info, experience, education, skills, languages)."""
    return await client().get_resume()


@tool(READ, "Preview application form")
async def get_apply_form(job: JobRef, company: Annotated[str | None, Field(description="Company slug if `job` is a bare id.")] = None) -> dict[str, Any]:
    """Show what Jobinja will pre-fill when applying to a job: name, email, phone, your current default
    resume, whether you already applied and whether phone verification is required. Sends nothing."""
    return await client().get_apply_form(job, company)


@tool(WRITE, "Apply to job (send resume)")
async def apply_to_job(
    job: JobRef,
    company: Annotated[str | None, Field(description="Company slug if `job` is a bare id.")] = None,
    resume_path: Annotated[str | None, Field(description="Local resume file to upload and send.")] = None,
    file_id: Annotated[str | None, Field(description="An already uploaded resume file_id (from upload_resume or get_apply_form).")] = None,
    use_profile: Annotated[bool, Field(description="Send your Jobinja profile (CV builder) resume instead of a file.")] = False,
    full_name: Annotated[str | None, Field(description="Override the applicant name.")] = None,
    email: Annotated[str | None, Field(description="Override the contact email.")] = None,
    phone: Annotated[str | None, Field(description="Override the contact mobile number.")] = None,
    create_job_alert: Annotated[bool, Field(description="Also subscribe to similar-job emails.")] = False,
    verification_code: Annotated[str | None, Field(description="SMS code, only when a previous call returned verification_required.")] = None,
) -> dict[str, Any]:
    """Submit a job application (ارسال رزومه). Without resume_path/file_id/use_profile it sends the default
    resume shown by get_apply_form. Confirm with the user before calling: this really applies."""
    return await client().apply(
        job, company, resume_path=resume_path, file_id=file_id, use_profile=use_profile, full_name=full_name,
        email=email, phone=phone, create_job_alert=create_job_alert, verification_code=verification_code,
    )


# =========================================================== applications
@tool(READ, "Applied jobs")
async def list_applied_jobs(
    status: Annotated[str | None, Field(description="Filter, e.g. 'pending', 'read', 'interview_accepted', 'hired', 'dismissed', 'applicant_canceled', 'expired'.")] = None,
    page: Page = 1,
    include_raw: Annotated[bool, Field(description="Include the full raw records from Jobinja.")] = False,
) -> dict[str, Any]:
    """Every job you applied to with its current status (pending, seen by employer, interview, hired, rejected...)."""
    return await client().applied_jobs(status, page, include_raw)


@tool(READ, "Application details")
async def get_application(application_id: Annotated[str, Field(description="Application short id from list_applied_jobs.")]) -> dict[str, Any]:
    """One application: status timeline, resume sent, contact number, cover letter and job info."""
    return await client().get_application(application_id)


@tool(DESTRUCTIVE, "Withdraw application")
async def cancel_application(
    application_id: Annotated[str, Field(description="Application short id.")],
    reason: Annotated[str, Field(description="Why you are withdrawing (shown to Jobinja).")],
) -> Any:
    """Withdraw (cancel) an application you sent. Confirm with the user first."""
    return await client().cancel_application(application_id, reason)


@tool(DESTRUCTIVE, "Remove application from list")
async def delete_application(application_id: Annotated[str, Field(description="Application short id.")]) -> Any:
    """Remove an application from your applied-jobs list. Confirm with the user first."""
    return await client().delete_application(application_id)


@tool(WRITE_IDEMPOTENT, "Update cover letter")
async def update_cover_letter(
    application_id: Annotated[str, Field(description="Application short id.")],
    cover_letter: Annotated[str, Field(description="Cover letter text.")],
    attachment_paths: Annotated[list[str] | None, Field(description="Optional local files to attach.")] = None,
) -> Any:
    """Add or replace the cover letter (and attachments) of an application."""
    return await client().update_cover_letter(application_id, cover_letter, attachment_paths)


# ============================================================== bookmarks
@tool(WRITE_IDEMPOTENT, "Save / unsave job")
async def save_job(
    job: JobRef,
    saved: Annotated[bool, Field(description="true to bookmark (نشان کردن), false to remove the bookmark.")] = True,
    company: Annotated[str | None, Field(description="Company slug if `job` is a bare id.")] = None,
) -> dict[str, Any]:
    """Bookmark or un-bookmark a job ad.

    Works without logging in: Jobinja then keeps a guest bookmark tied to this server's cookie session,
    and the result carries a `note` saying so. Log in first to keep bookmarks on the user's account."""
    return await client().set_job_saved(job, saved, company)


@tool(READ, "Saved jobs")
async def list_saved_jobs(page: Page = 1) -> dict[str, Any]:
    """Jobs you bookmarked."""
    return await client().saved_jobs(page)


@tool(READ, "Recommended jobs")
async def list_recommended_jobs(page: Page = 1) -> dict[str, Any]:
    """Jobs Jobinja recommends for your profile."""
    return await client().recommended_jobs(page)


# ============================================================= job alerts
AlertJobType = Literal["any", "full_time", "part_time", "internship", "remote"]


@tool(READ, "Job alerts")
async def list_job_alerts() -> dict[str, Any]:
    """Your job-alert email subscriptions."""
    return await client().job_alerts()


@tool(WRITE, "Create job alert")
async def create_job_alert(
    keywords: Annotated[str, Field(description="Keywords / job title to watch.")],
    categories: Annotated[list[str] | None, Field(description="Job categories.")] = None,
    cities: Annotated[list[str] | None, Field(description="Cities, e.g. ['تهران', 'اصفهان-اصفهان'].")] = None,
    job_type: AlertJobType = "any",
    frequency: Literal["daily", "weekly"] = "daily",
    email: Annotated[str | None, Field(description="Destination email (defaults to your account email).")] = None,
) -> Any:
    """Subscribe to email alerts for new matching jobs."""
    return await client().create_job_alert(keywords, categories, cities, job_type, frequency, email)


@tool(WRITE_IDEMPOTENT, "Update job alert")
async def update_job_alert(
    alert_id: Annotated[str, Field(description="Alert id from list_job_alerts.")],
    keywords: str,
    categories: list[str] | None = None,
    cities: list[str] | None = None,
    job_type: AlertJobType = "any",
    frequency: Literal["daily", "weekly"] = "daily",
    email: str | None = None,
) -> Any:
    """Change an existing job alert."""
    return await client().update_job_alert(alert_id, keywords, categories, cities, job_type, frequency, email)


@tool(DESTRUCTIVE, "Delete job alert")
async def delete_job_alert(alert_id: Annotated[str, Field(description="Alert id from list_job_alerts.")]) -> Any:
    """Delete a job alert."""
    return await client().delete_job_alert(alert_id)


@tool(READ, "Job alert results")
async def get_job_alert_results(alert_id: str, page: Page = 1) -> dict[str, Any]:
    """Jobs matched by one of your alerts."""
    return await client().job_alert_results(alert_id, page)


# ================================================================== misc
@tool(READ, "Notifications")
async def get_notifications() -> Any:
    """Your Jobinja notifications (application status changes, messages)."""
    return await client().notifications()


@tool(WRITE_IDEMPOTENT, "Mark notifications read")
async def mark_notifications_seen() -> Any:
    """Mark all notifications as seen."""
    return await client().mark_notifications_seen()


@tool(READ, "Report reasons")
async def get_report_reasons() -> list[str]:
    """Valid reasons for reporting a job ad."""
    return await client().report_reasons()


@tool(WRITE, "Report job ad")
async def report_job(
    job: JobRef,
    reason: Annotated[str, Field(description="One of get_report_reasons.")],
    details: Annotated[str, Field(description="Extra explanation.")] = "",
    company: str | None = None,
) -> Any:
    """Report an abusive, fake or miscategorised job ad to Jobinja."""
    return await client().report_job(job, reason, details, company)


@tool(WRITE_IDEMPOTENT, "Clear cache")
async def clear_cache() -> dict[str, int]:
    """Drop cached Jobinja pages so the next calls fetch fresh data."""
    return {"entries_removed": client().http.cache.clear()}


# ============================================================== resources
@mcp.resource("jobinja://search-options", mime_type="application/json", title="Jobinja search vocabularies")
async def search_options_resource() -> str:
    return json.dumps(await client().search_options(include_cities=True), ensure_ascii=False)


@mcp.resource("jobinja://jobs/{company}/{job_id}", mime_type="application/json", title="Jobinja job ad")
async def job_resource(company: str, job_id: str) -> str:
    return json.dumps(await client().get_job(job_id, company), ensure_ascii=False)


@mcp.resource("jobinja://companies/{slug}", mime_type="application/json", title="Jobinja company")
async def company_resource(slug: str) -> str:
    c = client()
    data = await c.get_company(slug)
    data["about"] = (await c.get_company_about(slug)).get("sections")
    data["jobs"] = (await c.get_company_jobs(slug, include_expired=False)).get("open_jobs")
    return json.dumps(data, ensure_ascii=False)


# ================================================================ prompts
@mcp.prompt(title="Find matching jobs")
def find_jobs(role: str, location: str = "تهران", seniority: str = "", must_have: str = "") -> str:
    """Search Jobinja for jobs that match a role and shortlist the best ones."""
    return (
        f"Find jobs on Jobinja for the role '{role}' in {location}."
        + (f" Seniority: {seniority}." if seniority else "")
        + (f" Must have: {must_have}." if must_have else "")
        + " Use get_search_options if you need valid filter values, run search_jobs (newest first, check 2-3 pages),"
        " open the promising ones with get_job, and present a ranked shortlist with title, company, salary, "
        "experience required, why it fits, and the URL. Don't apply to anything."
    )


@mcp.prompt(title="Apply to a job carefully")
def apply_carefully(job_url: str, resume_path: str = "") -> str:
    """Review a job and the applicant's resume, then apply only after explicit confirmation."""
    return (
        f"1. Read the job with get_job('{job_url}') and summarise its requirements.\n"
        "2. Check session_status; log in if needed.\n"
        f"3. Call get_apply_form to see the pre-filled data{' and plan to send ' + resume_path if resume_path else ''}.\n"
        "4. Show me everything that will be sent and wait for my explicit 'yes'.\n"
        "5. Then call apply_to_job. If it needs an SMS verification code, ask me for it.\n"
        "6. Finally confirm with list_applied_jobs."
    )


@mcp.prompt(title="Application status report")
def application_report() -> str:
    """Summarise the status of every application."""
    return (
        "Call list_applied_jobs (all pages) and produce a table grouped by status: job, company, applied date, "
        "current status. Highlight interviews and anything that changed recently; suggest follow-ups."
    )
