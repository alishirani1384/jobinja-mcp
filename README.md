# jobinja-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for [Jobinja.ir](https://jobinja.ir), Iran's largest job board.
It lets an AI assistant search and filter jobs, explore companies, upload a resume, apply, and track every application's status,
using the same pages and internal APIs the website itself uses.

- Python, official `mcp` SDK (FastMCP / `MCPServer` style), `httpx` + BeautifulSoup (`lxml`)
- Playwright only for the optional interactive browser login
- Response caching with `diskcache` (default) or Redis
- Transports: **stdio** (local) and **Streamable HTTP** (remote, optional bearer token)

Filters accept Persian or English names in any spelling (`"web, software development"`, `"وب برنامه نویسی"`, `"Tehran"`, `"تهران"`),
and unknown values fail with a list of valid options.

## Tools

| Area | Tools |
| --- | --- |
| Job search (`/jobs`) | `get_search_options`, `search_jobs`, `get_job`, `search_skills` |
| Companies (`/companies`) | `search_companies`, `list_company_categories`, `browse_companies_by_category`, `get_top_companies`, `get_company`, `get_company_about`, `get_company_jobs` |
| Session | `connect_account`, `import_browser_session`, `login`, `login_with_browser`, `import_cookies`, `session_status`, `logout`, `get_account` |
| Resume & applying | `upload_resume`, `get_my_resume`, `get_apply_form`, `apply_to_job` |
| Applications (`/jobs/applied`) | `list_applied_jobs`, `get_application`, `cancel_application`, `delete_application`, `update_cover_letter` |
| Saved & recommended | `save_job`, `list_saved_jobs`, `list_recommended_jobs` |
| Job alerts | `list_job_alerts`, `create_job_alert`, `update_job_alert`, `delete_job_alert`, `get_job_alert_results` |
| Other | `get_notifications`, `mark_notifications_seen`, `get_report_reasons`, `report_job`, `clear_cache` |

`search_jobs` supports keyword, job categories, provinces, job type, experience, minimum salary (Toman) or negotiable salary,
benefits (insurance, flexible hours, loans, ...), remote, internship, sorting (relevance / newest / highest salary) and paging.

`apply_to_job` sends a local resume file, an already uploaded `file_id`, your Jobinja profile resume, or the default resume shown by
`get_apply_form`. If Jobinja asks for SMS verification, the tool returns `verification_required`; call it again with `verification_code`.

Resources: `jobinja://search-options`, `jobinja://jobs/{company}/{job_id}`, `jobinja://companies/{slug}`.
Prompts: `find_jobs`, `apply_carefully`, `application_report`.

Public tools (search, companies, job details) work without an account. Resume, apply, application, alert and notification tools need a login.

## Install

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .            # add ".[browser]" for browser login, ".[redis]" for Redis, ".[dev]" for tests
.\.venv\Scripts\playwright install chromium # only if you installed the browser extra
```

On macOS/Linux use `.venv/bin/...` instead of `.\.venv\Scripts\...`.

## Logging in

Just ask the assistant to connect your Jobinja account. The `connect_account` tool tries, in order: the saved
session, `JOBINJA_EMAIL` / `JOBINJA_PASSWORD`, and your browser. If none of those work it asks you to paste one
thing:

1. Open https://jobinja.ir in the browser where you're logged in, press **F12** and open the **Network** tab.
2. Reload the page, right-click the first request (`jobinja.ir`) and choose **Copy > Copy as cURL** (cmd or bash).
3. Paste it into the chat. It contains your login, so only paste it there.

Jobinja's "remember me" cookie keeps that login working for about a year (until you log out of Jobinja in the
browser). `session_status` reports `long_lived_login: false` if the pasted cookies lack it, meaning the login will
only last about a day. `import_cookies` also accepts "Copy as PowerShell", the `cookie` request header, or a JSON
cookie export.

The individual methods, if you want to pick one yourself:

1. **Reuse your browser's login** (no password): `import_browser_session` takes the jobinja.ir session from a browser
   on this machine where you're already logged in. Only jobinja.ir cookies are read.
   - **Chrome, Edge, Brave** protect cookies with App-Bound encryption, which only the running browser can unlock.
     Enable `chrome://inspect/#remote-debugging` (Edge: `edge://inspect/#remote-debugging`) once, keep the browser
     open, run the tool, and click **Allow** when the browser asks.
   - **Firefox** works directly.
   - Older Chromium-based browsers without App-Bound encryption also work while closed: the tool copies the cookie
     file to a temporary folder and lets a hidden instance of the browser decrypt it.

   If you'd rather not enable remote debugging, copy the cookies by hand (option 4). It takes about 30 seconds and
   the session is saved, so you only do it once.
2. **Email and password**: set `JOBINJA_EMAIL` / `JOBINJA_PASSWORD`, or pass them to the `login` tool.
3. **Browser window**: `login_with_browser` opens Chromium so you can sign in with Google, LinkedIn or solve a captcha (needs the `browser` extra).
4. **Cookies by hand**: the "Copy as cURL" paste described above, passed to `import_cookies`.

With option 1 the browser and the server share one Jobinja session, so `logout` from the server logs the browser out too.

The session is saved to `~/.jobinja-mcp/session.json` and reused across restarts. `logout` deletes it.

## Running

```powershell
jobinja-mcp                                    # stdio (default)
jobinja-mcp --transport http --port 8000       # Streamable HTTP at http://127.0.0.1:8000/mcp
```

For a remote server, set `JOBINJA_MCP_AUTH_TOKEN`. Clients must then send `Authorization: Bearer <token>`.
Binding to a non-local host without a token logs a warning, since anyone who can reach the server can use your Jobinja session.

### Cursor / Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "jobinja": {
      "command": "C:\\path\\to\\jobinja-mcp\\.venv\\Scripts\\jobinja-mcp.exe",
      "env": { "JOBINJA_EMAIL": "you@example.com", "JOBINJA_PASSWORD": "..." }
    }
  }
}
```

### Remote (Streamable HTTP)

```json
{
  "mcpServers": {
    "jobinja": {
      "url": "https://your-host.example.com/mcp",
      "headers": { "Authorization": "Bearer <JOBINJA_MCP_AUTH_TOKEN>" }
    }
  }
}
```

## Configuration

All settings are environment variables; see [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `JOBINJA_EMAIL`, `JOBINJA_PASSWORD` | - | Credentials used by `login` when no arguments are given |
| `JOBINJA_DATA_DIR` | `~/.jobinja-mcp` | Session file and disk cache location |
| `JOBINJA_CACHE` | `1` | Set to `0` to disable caching |
| `JOBINJA_CACHE_TTL` | `600` | Seconds to cache public pages |
| `JOBINJA_REDIS_URL` | - | Use Redis instead of the disk cache |
| `JOBINJA_MIN_REQUEST_INTERVAL` | `0.4` | Minimum seconds between requests to Jobinja |
| `JOBINJA_MAX_RETRIES` | `3` | Retries on network errors, 429 and 5xx |
| `JOBINJA_TIMEOUT` | `30` | Request timeout in seconds |
| `JOBINJA_BROWSER_HEADLESS` | `0` | Run the Playwright login headless |
| `JOBINJA_MCP_TRANSPORT`, `JOBINJA_MCP_HOST`, `JOBINJA_MCP_PORT`, `JOBINJA_MCP_PATH` | `stdio`, `127.0.0.1`, `8000`, `/mcp` | Defaults for the CLI flags |
| `JOBINJA_MCP_AUTH_TOKEN` | - | Bearer token required by the HTTP transport |
| `JOBINJA_LOG_LEVEL` | `INFO` | Log level (logs go to stderr) |

Pages that depend on your account are never cached, and cache entries are separated between logged-in and guest sessions.

## Notes

- Jobinja allows bookmarking jobs as a guest. `save_job` then returns a `note`, and the bookmark lives only in this server's cookie session.
- Jobinja has no public API. This server reads its HTML and the JSON endpoints used by its frontend, so site changes may need parser updates.
  The parsers are covered by tests against saved pages in `tests/fixtures`.
- Use it for your own job search at a reasonable pace; requests are rate limited by default.

## Development

```powershell
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```
