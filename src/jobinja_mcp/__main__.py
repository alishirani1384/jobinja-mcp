from __future__ import annotations

import argparse
import hmac
import logging
import os
import sys


def _build_http_app(path: str, host: str, token: str | None):
    from starlette.responses import JSONResponse

    from .server import mcp

    try:
        app = mcp.streamable_http_app(streamable_http_path=path, host=host)
    except TypeError:  # mcp 1.x configures the app through settings
        mcp.settings.streamable_http_path = path
        mcp.settings.host = host
        app = mcp.streamable_http_app()
    if not token:
        return app

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            supplied = headers.get(b"authorization", b"").decode()
            if not hmac.compare_digest(supplied, f"Bearer {token}"):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jobinja-mcp", description="MCP server for Jobinja.ir")
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("JOBINJA_MCP_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.environ.get("JOBINJA_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("JOBINJA_MCP_PORT", "8000")))
    parser.add_argument("--path", default=os.environ.get("JOBINJA_MCP_PATH", "/mcp"))
    parser.add_argument("--log-level", default=os.environ.get("JOBINJA_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    # stdout carries the MCP protocol in stdio mode, so logs must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.transport == "stdio":
        from .server import mcp

        mcp.run(transport="stdio")
        return

    import uvicorn

    token = os.environ.get("JOBINJA_MCP_AUTH_TOKEN") or None
    if not token and args.host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("Serving on %s without JOBINJA_MCP_AUTH_TOKEN: anyone who can reach it can use your Jobinja session.", args.host)
    uvicorn.run(_build_http_app(args.path, args.host, token), host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
