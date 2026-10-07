import pytest

from jobinja_mcp.cookie_input import CookieInputError, parse_cookie_text

EXPECTED = {"device_id": "abc", "JSESSID": "eyJpdiI6IjEyMyJ9%3D", "remember_82e5": "eyJ", "XSRF-TOKEN": "tok%3D"}

CURL_BASH = """curl 'https://jobinja.ir/' \\
  -H 'accept: text/html,application/xhtml+xml' \\
  -H 'accept-language: en-US,en;q=0.9' \\
  -b 'device_id=abc; JSESSID=eyJpdiI6IjEyMyJ9%3D; remember_82e5=eyJ; XSRF-TOKEN=tok%3D' \\
  -H 'referer: https://www.google.com/' \\
  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)'"""

CURL_CMD = """curl ^"https://jobinja.ir/^" ^
  -H ^"accept: text/html,application/xhtml+xml^" ^
  -b ^"device_id=abc; JSESSID=eyJpdiI6IjEyMyJ9^%^3D; remember_82e5=eyJ; XSRF-TOKEN=tok^%^3D^" ^
  -H ^"user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)^\""""

CURL_OLD_HEADER = """curl 'https://jobinja.ir/jobs' -H 'cookie: device_id=abc; JSESSID=eyJpdiI6IjEyMyJ9%3D; remember_82e5=eyJ; XSRF-TOKEN=tok%3D' --compressed"""

POWERSHELL = """$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$session.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
$session.Cookies.Add((New-Object System.Net.Cookie("device_id", "abc", "/", ".jobinja.ir")))
$session.Cookies.Add((New-Object System.Net.Cookie("JSESSID", "eyJpdiI6IjEyMyJ9%3D", "/", ".jobinja.ir")))
$session.Cookies.Add((New-Object System.Net.Cookie("remember_82e5", "eyJ", "/", ".jobinja.ir")))
$session.Cookies.Add((New-Object System.Net.Cookie("XSRF-TOKEN", "tok%3D", "/", ".jobinja.ir")))
Invoke-WebRequest -UseBasicParsing -Uri "https://jobinja.ir/" `
-WebSession $session"""

HEADER_BLOCK = """accept: text/html
cookie: device_id=abc; JSESSID=eyJpdiI6IjEyMyJ9%3D; remember_82e5=eyJ; XSRF-TOKEN=tok%3D
user-agent: Mozilla/5.0"""

JSON_EXPORT = """[
 {"domain": ".jobinja.ir", "name": "device_id", "value": "abc"},
 {"domain": ".jobinja.ir", "name": "JSESSID", "value": "eyJpdiI6IjEyMyJ9%3D"},
 {"domain": "jobinja.ir", "name": "remember_82e5", "value": "eyJ"},
 {"domain": ".jobinja.ir", "name": "XSRF-TOKEN", "value": "tok%3D"},
 {"domain": ".google.com", "name": "SID", "value": "not-ours"}
]"""


@pytest.mark.parametrize(
    "pasted",
    [
        CURL_BASH,
        CURL_CMD,
        CURL_OLD_HEADER,
        POWERSHELL,
        HEADER_BLOCK,
        JSON_EXPORT,
        "cookie: device_id=abc; JSESSID=eyJpdiI6IjEyMyJ9%3D; remember_82e5=eyJ; XSRF-TOKEN=tok%3D",
        "  device_id=abc; JSESSID=eyJpdiI6IjEyMyJ9%3D; remember_82e5=eyJ; XSRF-TOKEN=tok%3D \n",
    ],
    ids=["curl-bash", "curl-cmd", "curl-cookie-header", "powershell", "header-block", "json", "header", "bare"],
)
def test_every_copy_format(pasted):
    assert {c["name"]: c["value"] for c in parse_cookie_text(pasted)} == EXPECTED


def test_rejects_requests_to_other_sites():
    with pytest.raises(CookieInputError, match="github.com, not jobinja.ir"):
        parse_cookie_text("curl 'https://github.com/' -b 'user_session=x'")


@pytest.mark.parametrize("pasted", ["", "   ", "hello there", "curl 'https://jobinja.ir/' -H 'accept: */*'"])
def test_nothing_usable(pasted):
    with pytest.raises(CookieInputError):
        parse_cookie_text(pasted)
