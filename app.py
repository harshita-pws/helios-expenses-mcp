"""ASGI entry point for hosting the MCP server over HTTP (Render, Fly, etc.).

Render runs:  uvicorn app:app --host 0.0.0.0 --port $PORT

Streamable HTTP is used in stateless mode: every request carries its own
context, so nothing breaks when the free instance spins down mid-conversation
or a proxy drops a connection.
"""

from __future__ import annotations

import logging
import os

from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import Receive, Scope, Send

from mcp.server.transport_security import TransportSecuritySettings

from helios_expenses.server import mcp, store, workbook_path

logger = logging.getLogger("helios-expenses.http")

MCP_PATH = os.environ.get("MCP_PATH", "/mcp")
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()

# Paths served without a token, so a browser or Render's health check can hit them.
PUBLIC_PATHS = ("/", "/healthz")


def _allowed_hosts() -> list[str]:
    """Host header values to accept.

    DNS-rebinding protection is on by default and there is no wildcard for a
    whole host, so the deployed hostname has to be listed explicitly. Render
    injects RENDER_EXTERNAL_HOSTNAME; anything else goes in MCP_ALLOWED_HOSTS
    as a comma-separated list.
    """
    hosts: list[str] = []
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        hosts += [render_host, f"{render_host}:*"]
    for extra in os.environ.get("MCP_ALLOWED_HOSTS", "").split(","):
        extra = extra.strip()
        if extra:
            hosts.append(extra)
    hosts += ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    return list(dict.fromkeys(hosts))


class BearerTokenMiddleware:
    """Require `Authorization: Bearer <token>` when MCP_AUTH_TOKEN is set.

    Without a token the deployment is world-readable. That is a deliberate
    opt-in: the workbook is synthetic, but set the variable for anything real.
    """

    def __init__(self, app: object, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            return await self.app(scope, receive, send)  # type: ignore[operator]

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        supplied = headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        # Length-safe comparison is not required for a shared deployment token,
        # but constant-time keeps timing noise out of it.
        import hmac

        if not hmac.compare_digest(supplied, expected):
            response = JSONResponse(
                {"error": "unauthorized", "detail": "Send Authorization: Bearer <token>."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)  # type: ignore[operator]


@mcp.custom_route("/", methods=["GET"])
async def index(_request: object) -> PlainTextResponse:
    """Human-readable landing page, so hitting the URL in a browser explains itself."""
    s = store()
    return PlainTextResponse(
        "helios-expenses MCP server\n\n"
        f"workbook: {s.path.name} ({s.tables['expenses'].rows} expense rows)\n"
        f"endpoint: {MCP_PATH} (streamable HTTP, POST)\n"
        f"auth:     {'Bearer token required' if AUTH_TOKEN else 'none (open)'}\n"
        "health:   /healthz\n\n"
        "This is an MCP endpoint, not a REST API - connect an MCP client:\n"
        f"  claude mcp add --transport http helios-expenses <this-url>{MCP_PATH}\n"
    )


def build_app() -> object:
    xlsx = workbook_path()
    if not xlsx.exists():
        raise SystemExit(f"Workbook not found: {xlsx}. Commit it to data/ or set HELIOS_XLSX.")

    s = store()  # load at boot, not on the first request, so /healthz is meaningful
    logger.info("loaded %s: %s", s.path.name, {t.name: t.rows for t in s.tables.values()})
    if not AUTH_TOKEN:
        logger.warning("MCP_AUTH_TOKEN is not set - this endpoint is open to anyone.")

    application = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts()),
    )
    if AUTH_TOKEN:
        return BearerTokenMiddleware(application, AUTH_TOKEN)
    return application


app = build_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
