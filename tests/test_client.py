import asyncio
import json

import httpx
import pytest
from conftest import fixture_json

from jobinja_mcp.client import Jobinja
from jobinja_mcp.http import AuthRequired, JobinjaError

API_FIXTURES = {
    "/api/v10/job_search_meta": "api_meta.json",
    "/api/v10/job/categories": "api_cats.json",
    "/api/v10/region/province": "api_prov.json",
}


def run(coro):
    return asyncio.run(coro)


def mock_client(settings, handler) -> Jobinja:
    jb = Jobinja(settings)
    jb.http._client = httpx.AsyncClient(base_url=settings.base_url, transport=httpx.MockTransport(handler))
    return jb


def fixture_api(request: httpx.Request) -> httpx.Response:
    name = API_FIXTURES.get(request.url.path)
    if not name:
        return httpx.Response(404, json={"message": "not found"})
    return httpx.Response(200, json=fixture_json(name))


def test_build_search_params_resolves_english_names(settings):
    async def go():
        jb = mock_client(settings, fixture_api)
        try:
            return await jb.build_search_params(
                keyword=" react ",
                categories=["web, software development"],
                provinces=["Tehran"],
                job_types=["full_time"],
                min_salary_toman=40_000_000,
                remote=True,
                sort_by="newest",
                page=2,
            )
        finally:
            await jb.aclose()

    params = run(go())
    assert ("filters[keywords][0]", "react") in params
    assert ("filters[job_categories][]", "وب،‌ برنامه‌نویسی و نرم‌افزار") in params
    assert ("filters[locations][]", "تهران") in params
    assert ("filters[sal_min][]", "40000000:") in params
    assert ("filters[remote]", "1") in params
    assert ("page", "2") in params
    assert any(k == "filters[job_types][]" for k, _ in params)
    assert dict(params)["sort_by"] != "relevance"


def test_build_search_params_rejects_unknown_province(settings):
    async def go():
        jb = mock_client(settings, fixture_api)
        try:
            await jb.build_search_params(provinces=["Atlantis"])
        finally:
            await jb.aclose()

    with pytest.raises(ValueError, match="Unknown province"):
        run(go())


def test_account_endpoints_map_guest_errors_to_auth_required(settings):
    async def go(authenticated):
        jb = mock_client(settings, lambda r: httpx.Response(404, json={"message": "api_not_found"}))
        jb.http.authenticated = authenticated
        try:
            await jb.get_resume()
        finally:
            await jb.aclose()

    with pytest.raises(AuthRequired):
        run(go(False))
    # A logged-in user hitting a real 404 should see the actual error.
    with pytest.raises(JobinjaError) as exc:
        run(go(True))
    assert not isinstance(exc.value, AuthRequired) and exc.value.status == 404


def fake_jobinja(request: httpx.Request) -> httpx.Response:
    """Home page that is logged in only when the 'remember me' cookie is sent."""
    logged_in = "remember_82e5=" in request.headers.get("cookie", "")
    return httpx.Response(200, text=f"<script>var isAuthenticated = !!parseInt('{int(logged_in)}');</script>")


def test_connect_account_asks_for_paste_then_logs_in(settings):
    async def go():
        jb = mock_client(settings, fake_jobinja)
        try:
            first = await jb.connect_account(try_browser=False)
            second = await jb.connect_account(cookies="curl 'https://jobinja.ir/' -b 'JSESSID=s; remember_82e5=r'")
            third = await jb.connect_account(try_browser=False)
            return first, second, third
        finally:
            await jb.aclose()

    first, second, third = run(go())
    assert first["logged_in"] is False and first["next_step"] == "ask_user_to_paste"
    assert any("Copy as cURL" in step for step in first["instructions_for_user"])
    assert "email/password: not configured" in " ".join(first["tried"])
    assert second["method"] == "pasted_cookies" and second["logged_in"] is True
    assert second["long_lived_login"] is True
    assert third["method"] == "saved_session" and third["logged_in"] is True


def test_import_without_remember_cookie_warns(settings):
    def only_session(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<script>var isAuthenticated = !!parseInt('1');</script>")

    async def go():
        jb = mock_client(settings, only_session)
        try:
            return await jb.import_cookies("cookie: JSESSID=s")
        finally:
            await jb.aclose()

    result = run(go())
    assert result["logged_in"] is True and result["long_lived_login"] is False
    assert "within a day" in result["note"]


def test_save_job_as_guest_adds_note(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/jobs/tEk6/favorites":
            body = {"status": "success", "message": "saved", "data": {"favorite": {"user_type": "guests"}}}
            return httpx.Response(201, json=body)
        if request.url.path == "/":
            return httpx.Response(200, text='<meta name="csrf-token" content="tok">')
        return httpx.Response(404)

    async def go():
        jb = mock_client(settings, handler)
        try:
            return await jb.set_job_saved("hesaba/tEk6", True)
        finally:
            await jb.aclose()

    result = run(go())
    assert result["saved"] is True and result["job_id"] == "tEk6"
    assert "guest" in result["note"]
    assert json.loads(settings.session_file.read_text(encoding="utf8")) is not None
