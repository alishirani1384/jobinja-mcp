"""Reuse the jobinja.ir session of a browser the user is already logged in with.

Routes, tried in this order:

1. Live: if remote debugging is enabled at chrome://inspect/#remote-debugging (Chrome 144+; Edge/Brave have
   the same page), ask the running browser for its cookies over the DevTools protocol.
2. Relaunch (no setup, browser must be closed): copy the profile's cookie DB and key file to a temp folder,
   start a hidden instance of the same browser on the copy and read the cookies over DevTools. The browser
   decrypts its own cookies, so this works with App-Bound (`v20`) encryption. Session-only cookies live in
   memory and are not on disk, so this finds Jobinja's persistent "remember me" login.
3. Disk: Firefox stores cookie values in plain text. Chromium browsers whose program can't be found fall back
   to DPAPI decryption, which only handles pre-App-Bound (`v10`) cookies.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

JOBINJA_DOMAIN = "jobinja.ir"
SESSION_COOKIE_HINTS = ("jsessid", "session")

_HOME = Path.home()
_LOCAL = Path(os.environ.get("LOCALAPPDATA") or _HOME / "AppData/Local")
_ROAMING = Path(os.environ.get("APPDATA") or _HOME / "AppData/Roaming")
_MAC = _HOME / "Library/Application Support"
_LINUX = _HOME / ".config"

_PF = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
_PF86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
CHROMIUM_EXES: dict[str, list[Path]] = {
    "chrome": [_PF / "Google/Chrome/Application/chrome.exe", _PF86 / "Google/Chrome/Application/chrome.exe", _LOCAL / "Google/Chrome/Application/chrome.exe"],
    "edge": [_PF86 / "Microsoft/Edge/Application/msedge.exe", _PF / "Microsoft/Edge/Application/msedge.exe"],
    "brave": [_PF / "BraveSoftware/Brave-Browser/Application/brave.exe", _PF86 / "BraveSoftware/Brave-Browser/Application/brave.exe", _LOCAL / "BraveSoftware/Brave-Browser/Application/brave.exe"],
    "vivaldi": [_LOCAL / "Vivaldi/Application/vivaldi.exe", _PF / "Vivaldi/Application/vivaldi.exe"],
    "opera": [_LOCAL / "Programs/Opera/opera.exe", _PF / "Opera/opera.exe"],
    "chromium": [_PF / "Chromium/Application/chrome.exe", _LOCAL / "Chromium/Application/chrome.exe"],
}

CHROMIUM_DIRS: dict[str, dict[str, Path]] = {
    "chrome": {"win32": _LOCAL / "Google/Chrome/User Data", "darwin": _MAC / "Google/Chrome", "linux": _LINUX / "google-chrome"},
    "edge": {"win32": _LOCAL / "Microsoft/Edge/User Data", "darwin": _MAC / "Microsoft Edge", "linux": _LINUX / "microsoft-edge"},
    "brave": {
        "win32": _LOCAL / "BraveSoftware/Brave-Browser/User Data",
        "darwin": _MAC / "BraveSoftware/Brave-Browser",
        "linux": _LINUX / "BraveSoftware/Brave-Browser",
    },
    "vivaldi": {"win32": _LOCAL / "Vivaldi/User Data", "darwin": _MAC / "Vivaldi", "linux": _LINUX / "vivaldi"},
    "opera": {"win32": _ROAMING / "Opera Software/Opera Stable", "darwin": _MAC / "com.operasoftware.Opera", "linux": _LINUX / "opera"},
    "chromium": {"win32": _LOCAL / "Chromium/User Data", "darwin": _MAC / "Chromium", "linux": _LINUX / "chromium"},
}
DISPLAY_NAMES = {"chrome": "Chrome", "edge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi", "opera": "Opera", "chromium": "Chromium", "firefox": "Firefox"}
FIREFOX_DIRS = {"win32": _ROAMING / "Mozilla/Firefox/Profiles", "darwin": _MAC / "Firefox/Profiles", "linux": _HOME / ".mozilla/firefox"}
BROWSERS = (*CHROMIUM_DIRS, "firefox")


class BrowserCookieError(Exception):
    pass


@dataclass
class BrowserSession:
    browser: str
    profile: str | None
    method: str  # "live" or "disk"
    cookies: list[dict[str, Any]] = field(default_factory=list)

    @property
    def score(self) -> int:
        """2 for a remembered login, 1 for a plain session cookie (guests get one too), 0 otherwise."""
        names = [c["name"].lower() for c in self.cookies]
        # Laravel's "remember me" cookie: remember_web_<hash> or, on Jobinja, remember_<hash>.
        if any(n.startswith("remember_") for n in names):
            return 2
        return 1 if any(h in n for n in names for h in SESSION_COOKIE_HINTS) else 0


def _platform() -> str:
    return "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"


def _installed(browser: str) -> bool:
    if browser == "firefox":
        return FIREFOX_DIRS[_platform()].exists()
    return CHROMIUM_DIRS[browser][_platform()].exists()


def _is_jobinja(domain: str) -> bool:
    d = domain.lstrip(".").lower()
    return d == JOBINJA_DOMAIN or d.endswith("." + JOBINJA_DOMAIN)


def _copy_db(path: Path) -> Path:
    """Copy a cookie database (plus WAL) so a running browser's lock does not matter."""
    tmp = Path(tempfile.mkdtemp(prefix="jobinja-cookies-"))
    try:
        shutil.copy2(path, tmp / path.name)
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                shutil.copy2(side, tmp / side.name)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise BrowserCookieError(
            "the cookie database is locked by the running browser. Enable live access at "
            "chrome://inspect/#remote-debugging (keep the browser open) and try again; for Edge, Brave or Firefox, "
            "closing the browser completely also works."
        ) from exc
    return tmp / path.name


# ------------------------------------------------------------------ live (CDP)
async def _live_cookies(browser: str, user_data: Path, timeout: float) -> BrowserSession:
    port_file = user_data / "DevToolsActivePort"
    if not port_file.exists():
        raise BrowserCookieError(f"{browser}: remote debugging is off (no {port_file.name}).")
    lines = port_file.read_text(encoding="utf8").split()
    if len(lines) < 2:
        raise BrowserCookieError(f"{browser}: unreadable {port_file}.")
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise BrowserCookieError("The 'websockets' package is required for live browser access.") from exc

    url = f"ws://127.0.0.1:{lines[0]}{lines[1]}"

    async def fetch() -> dict[str, Any]:
        async with connect(url, max_size=64 * 1024 * 1024, open_timeout=timeout) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == 1:
                    return msg

    try:
        # Chrome asks the user to allow the connection, so give them time to click "Allow".
        msg = await asyncio.wait_for(fetch(), timeout)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise BrowserCookieError(f"{browser}: timed out waiting for the browser. Did you click 'Allow' in its prompt?") from exc
    except OSError as exc:
        raise BrowserCookieError(f"{browser}: could not connect to {url} ({exc}). Is the browser still running?") from exc
    if "error" in msg:
        raise BrowserCookieError(f"{browser}: DevTools error {msg['error']}")
    cookies = [
        {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/"), "httpOnly": c.get("httpOnly")}
        for c in msg.get("result", {}).get("cookies", [])
        if _is_jobinja(c.get("domain", ""))
    ]
    return BrowserSession(browser, None, "live", cookies)


# ------------------------------------------------ relaunch: copy + hidden browser
def _browser_exe(browser: str) -> Path | None:
    return next((p for p in CHROMIUM_EXES.get(browser, []) if p.exists()), None)


def _cookie_db(prof: Path) -> Path | None:
    return next((d for d in (prof / "Network/Cookies", prof / "Cookies") if d.exists()), None)


def _profile_jobinja_cookies(db: Path) -> tuple[int, bool]:
    """(number of jobinja.ir cookies on disk, whether they're App-Bound `v20` encrypted)."""
    copy = _copy_db(db)
    try:
        con = sqlite3.connect(copy)
        prefixes = [
            bytes(p or b"")
            for (p,) in con.execute("SELECT substr(encrypted_value, 1, 3) FROM cookies WHERE host_key LIKE ?", (f"%{JOBINJA_DOMAIN}",))
        ]
        con.close()
        return len(prefixes), bool(prefixes) and all(p == b"v20" for p in prefixes)
    finally:
        shutil.rmtree(copy.parent, ignore_errors=True)


async def _relaunch_profile(browser: str, exe: Path, user_data: Path, prof: Path, timeout: float) -> BrowserSession:
    """Copy one profile's cookie DB + the key file to a temp folder and let a hidden browser decrypt it.

    The browser decrypts its own cookies (including App-Bound `v20`), so no key handling happens here.
    Only the temp copy is opened; the user's real profile is never written to.
    """
    db = _cookie_db(prof)
    tmp = Path(tempfile.mkdtemp(prefix="jobinja-relaunch-"))
    proc: subprocess.Popen | None = None
    try:
        shutil.copy2(user_data / "Local State", tmp / "Local State")
        dest = tmp / "Default" / db.relative_to(prof)
        dest.parent.mkdir(parents=True)
        shutil.copy2(db, dest)
        for suffix in ("-wal", "-shm", "-journal"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                shutil.copy2(side, dest.with_name(dest.name + suffix))
        proc = subprocess.Popen(
            [str(exe), "--headless=new", f"--user-data-dir={tmp}", "--remote-debugging-port=0",
             "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-extensions", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        port_file = tmp / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while not (port_file.exists() and len(port_file.read_text(encoding="utf8").split()) >= 2):
            if proc.poll() is not None or time.monotonic() > deadline:
                raise BrowserCookieError(f"{browser}: the hidden browser didn't start.")
            await asyncio.sleep(0.3)
        session = await _live_cookies(browser, tmp, timeout)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    session.profile, session.method = prof.name, "relaunch"
    return session


async def _relaunch_chromium(browser: str, user_data: Path, profile: str | None, timeout: float) -> list[BrowserSession]:
    exe = _browser_exe(browser)
    if exe is None:
        raise BrowserCookieError(f"{browser}: couldn't find the browser program to decrypt its cookies.")
    profiles = [p for p in _chromium_profiles(user_data) if profile in (None, p.name) and _cookie_db(p)]
    if not profiles:
        raise BrowserCookieError(f"{browser}: no profile named {profile!r}." if profile else f"{browser}: no profiles found.")
    label = DISPLAY_NAMES.get(browser, browser)
    try:
        on_disk = {p: _profile_jobinja_cookies(_cookie_db(p)) for p in profiles}
    except BrowserCookieError as exc:
        raise BrowserCookieError(
            f"{label} is running, so its cookies are locked. Close {label} completely (every window, and quit it from "
            "the system tray if it keeps running in the background), then try again. Or keep it open and enable "
            "chrome://inspect/#remote-debugging."
        ) from exc
    sessions: list[BrowserSession] = []
    app_bound: list[str] = []
    for prof, (count, is_v20) in on_disk.items():
        if not count:
            continue
        session = await _relaunch_profile(browser, exe, user_data, prof, timeout)
        if session.cookies:
            sessions.append(session)
        elif is_v20:
            app_bound.append(prof.name)
    if app_bound and not sessions:
        # Verified: App-Bound keys only decrypt for the original profile folder, and the browser refuses remote
        # debugging on its default folder, so a copy can't be decrypted by any launch mode.
        raise BrowserCookieError(
            f"{label} has a jobinja.ir login in profile {', '.join(app_bound)}, but it's protected by {label}'s App-Bound "
            "encryption, which only the running browser can unlock. Either enable chrome://inspect/#remote-debugging "
            f"in {label} (keep it open, then click Allow), or copy the cookies by hand into `import_cookies`."
        )
    return sessions


# ------------------------------------------------------------- disk: chromium
def _dpapi_decrypt(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = Blob(len(data), buf), Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise BrowserCookieError("Windows refused to decrypt the browser key (DPAPI). Run as the same Windows user.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _chromium_key(user_data: Path) -> bytes:
    state = json.loads((user_data / "Local State").read_text(encoding="utf8"))
    encrypted = base64.b64decode(state["os_crypt"]["encrypted_key"])
    if not encrypted.startswith(b"DPAPI"):
        raise BrowserCookieError("Unexpected browser key format.")
    return _dpapi_decrypt(encrypted[5:])


def _chromium_profiles(user_data: Path) -> list[Path]:
    if (user_data / "Network/Cookies").exists() or (user_data / "Cookies").exists():
        return [user_data]  # Opera keeps a single profile at the root
    return sorted(p for p in user_data.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile ")))


def _disk_chromium(browser: str, user_data: Path, profile: str | None) -> list[BrowserSession]:
    if _platform() != "win32":
        raise BrowserCookieError(
            f"{browser}: reading cookies from disk is only supported on Windows; enable live access at "
            "chrome://inspect/#remote-debugging instead."
        )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    profiles = [p for p in _chromium_profiles(user_data) if profile in (None, p.name)]
    if not profiles:
        raise BrowserCookieError(f"{browser}: no profile named {profile!r} in {user_data}.")
    key: bytes | None = None
    sessions: list[BrowserSession] = []
    problems: list[str] = []
    for prof in profiles:
        db = next((d for d in (prof / "Network/Cookies", prof / "Cookies") if d.exists()), None)
        if db is None:
            continue
        try:
            copy = _copy_db(db)
        except BrowserCookieError as exc:
            problems.append(f"{browser} ({prof.name}): {exc}")
            continue
        try:
            con = sqlite3.connect(copy)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            rows = con.execute(
                "SELECT host_key, name, value, encrypted_value, path FROM cookies WHERE host_key LIKE ?", (f"%{JOBINJA_DOMAIN}",)
            ).fetchall()
            con.close()
        finally:
            shutil.rmtree(copy.parent, ignore_errors=True)
        # Since DB version 24 the plaintext is prefixed with SHA-256(host_key).
        hashed_prefix = int(meta.get("version", 0)) >= 24
        cookies, app_bound = [], 0
        for host, name, value, enc, path in rows:
            if not _is_jobinja(host):
                continue
            if not value and enc:
                prefix = bytes(enc[:3])
                if prefix == b"v20":
                    app_bound += 1
                    continue
                if prefix in (b"v10", b"v11"):
                    key = key or _chromium_key(user_data)
                    plain = AESGCM(key).decrypt(bytes(enc[3:15]), bytes(enc[15:]), None)
                    value = (plain[32:] if hashed_prefix else plain).decode("utf8", "replace")
                else:
                    value = _dpapi_decrypt(bytes(enc)).decode("utf8", "replace")
            cookies.append({"name": name, "value": value, "domain": host, "path": path or "/"})
        if app_bound and not cookies:
            problems.append(
                f"{browser} ({prof.name}) protects its cookies with App-Bound Encryption, which can't be read from disk. "
                "Enable live access at chrome://inspect/#remote-debugging (keep the browser open) and try again."
            )
            continue
        sessions.append(BrowserSession(browser, prof.name, "disk", cookies))
    if problems and not any(s.score for s in sessions):
        raise BrowserCookieError("\n- ".join(problems))
    return sessions


# -------------------------------------------------------------- disk: firefox
def _disk_firefox(profile: str | None) -> list[BrowserSession]:
    root = FIREFOX_DIRS[_platform()]
    if not root.exists():
        raise BrowserCookieError("firefox: not installed.")
    sessions = []
    for prof in sorted(p for p in root.iterdir() if (p / "cookies.sqlite").exists()):
        if profile not in (None, prof.name):
            continue
        copy = _copy_db(prof / "cookies.sqlite")
        try:
            con = sqlite3.connect(copy)
            rows = con.execute("SELECT host, name, value, path FROM moz_cookies WHERE host LIKE ?", (f"%{JOBINJA_DOMAIN}",)).fetchall()
            con.close()
        finally:
            shutil.rmtree(copy.parent, ignore_errors=True)
        cookies = [{"name": n, "value": v, "domain": h, "path": p or "/"} for h, n, v, p in rows if _is_jobinja(h)]
        sessions.append(BrowserSession("firefox", prof.name, "disk", cookies))
    return sessions


# ---------------------------------------------------------------------- entry
async def find_jobinja_session(browser: str = "auto", profile: str | None = None, timeout: float = 60.0) -> BrowserSession:
    """Return the cookies of the browser profile most likely to hold a logged-in jobinja.ir session."""
    browser = browser.lower().strip()
    if browser != "auto" and browser not in BROWSERS:
        raise BrowserCookieError(f"Unknown browser '{browser}'. Use one of: auto, {', '.join(BROWSERS)}.")
    candidates = BROWSERS if browser == "auto" else (browser,)
    installed = [b for b in candidates if _installed(b)]
    if not installed:
        raise BrowserCookieError(f"No supported browser found ({', '.join(candidates)}).")

    problems: list[str] = []
    found: list[BrowserSession] = []
    for name in installed:
        if name == "firefox":
            attempts = [lambda: _disk_firefox(profile)]
        else:
            user_data = CHROMIUM_DIRS[name][_platform()]
            attempts = []
            if (user_data / "DevToolsActivePort").exists() and profile is None:
                attempts.append(lambda ud=user_data, n=name: _live_cookies(n, ud, timeout))
            if _browser_exe(name):
                attempts.append(lambda ud=user_data, n=name: _relaunch_chromium(n, ud, profile, timeout))
            else:
                attempts.append(lambda ud=user_data, n=name: _disk_chromium(n, ud, profile))
        for attempt in attempts:
            try:
                result = attempt()
                if asyncio.iscoroutine(result):
                    result = await result
            except BrowserCookieError as exc:
                problems.append(str(exc))
                continue
            sessions = [s for s in (result if isinstance(result, list) else [result]) if s.cookies]
            if not sessions:
                problems.append(f"{name}: no jobinja.ir cookies")
            found.extend(sessions)
            break  # this route worked; don't try the other route for the same browser
    if found:
        return max(found, key=lambda s: s.score)
    detail = "\n- ".join(problems) if problems else "none of them has jobinja.ir cookies"
    raise BrowserCookieError(f"No logged-in jobinja.ir session found in: {', '.join(installed)}.\n- {detail}")
