import asyncio
import sqlite3

import pytest

from jobinja_mcp import browser_cookies as bc


def make_firefox_profile(root, name, cookies):
    prof = root / name
    prof.mkdir(parents=True)
    con = sqlite3.connect(prof / "cookies.sqlite")
    con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, path TEXT)")
    con.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?, ?)", cookies)
    con.commit()
    con.close()


@pytest.fixture
def firefox_only(tmp_path, monkeypatch):
    root = tmp_path / "Profiles"
    monkeypatch.setitem(bc.FIREFOX_DIRS, bc._platform(), root)
    monkeypatch.setattr(bc, "_installed", lambda b: b == "firefox")
    return root


def test_firefox_prefers_logged_in_profile_and_only_reads_jobinja(firefox_only):
    make_firefox_profile(firefox_only, "guest.default", [(".jobinja.ir", "JSESSID", "g", "/")])
    make_firefox_profile(
        firefox_only,
        "me.default-release",
        [
            (".jobinja.ir", "JSESSID", "s", "/"),
            ("jobinja.ir", "remember_web_59ba36", "r", "/"),
            (".notjobinja.ir", "x", "1", "/"),
            (".google.com", "SID", "secret", "/"),
        ],
    )
    found = asyncio.run(bc.find_jobinja_session("auto"))
    assert (found.browser, found.profile, found.method) == ("firefox", "me.default-release", "disk")
    assert sorted(c["name"] for c in found.cookies) == ["JSESSID", "remember_web_59ba36"]


def test_no_cookies_reports_each_browser(firefox_only):
    make_firefox_profile(firefox_only, "empty", [(".google.com", "SID", "x", "/")])
    with pytest.raises(bc.BrowserCookieError, match="firefox: no jobinja.ir cookies"):
        asyncio.run(bc.find_jobinja_session("firefox"))


def test_unknown_browser():
    with pytest.raises(bc.BrowserCookieError, match="Unknown browser 'safari'"):
        asyncio.run(bc.find_jobinja_session("safari"))


@pytest.mark.skipif(bc._browser_exe("chrome") is None, reason="Chrome not installed")
def test_relaunch_reads_persistent_login_from_closed_profile(tmp_path):
    import json
    import subprocess
    import time

    from websockets.asyncio.client import connect

    profile = tmp_path / "chrome-data"

    async def cdp(method, params=None):
        port_file = profile / "DevToolsActivePort"
        for _ in range(60):
            if port_file.exists() and len(port_file.read_text().split()) >= 2:
                break
            await asyncio.sleep(0.5)
        port, path = port_file.read_text().split()[:2]
        async with connect(f"ws://127.0.0.1:{port}{path}") as ws:
            await ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while (msg := json.loads(await ws.recv())).get("id") != 1:
                pass

    async def seed_and_close():
        year = time.time() + 365 * 86400
        await cdp("Storage.setCookies", {"cookies": [
            {"name": "remember_web_abc", "value": "LOGGEDIN", "domain": ".jobinja.ir", "path": "/", "expires": year, "httpOnly": True, "secure": True},
            {"name": "SID", "value": "x", "domain": ".google.com", "path": "/", "expires": year},
        ]})
        await cdp("Browser.close")

    proc = subprocess.Popen([str(bc._browser_exe("chrome")), "--headless=new", f"--user-data-dir={profile}",
                             "--remote-debugging-port=0", "--no-first-run", "about:blank"])
    asyncio.run(seed_and_close())
    proc.wait(30)
    (profile / "DevToolsActivePort").unlink(missing_ok=True)

    sessions = asyncio.run(bc._relaunch_chromium("chrome", profile, None, timeout=30))
    assert [(s.profile, s.method, s.score) for s in sessions] == [("Default", "relaunch", 2)]
    assert [(c["name"], c["value"]) for c in sessions[0].cookies] == [("remember_web_abc", "LOGGEDIN")]


@pytest.mark.parametrize(
    ("names", "score"),
    [(["remember_web_x", "JSESSID"], 2), (["JSESSID", "XSRF-TOKEN"], 1), (["XSRF-TOKEN"], 0)],
)
def test_score(names, score):
    s = bc.BrowserSession("chrome", None, "live", [{"name": n, "value": "", "domain": ".jobinja.ir"} for n in names])
    assert s.score == score
