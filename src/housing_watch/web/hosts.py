"""The Host allowlist for the web UI and the MCP server (HW_ALLOWED_HOSTS, comma-separated).

The web UI has no login of its own, and DNS rebinding beats its same-origin check: the attacker's page resolves
its own name to the server, so the browser sends matching Origin and Host for that name. Refusing every Host not
listed here closes it. An entry matches the Host header exactly, port included as sent (`host:*` takes any
port). The default is the one name the deploy is reached by (only through Traefik), plus loopback for dev and
the container's own checks.
"""

from __future__ import annotations

import os

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

DEFAULT = ",".join((
    "housing.kanishksachdev.com",  # Traefik keeps the client's Host; https sends no port
    "localhost:*", "127.0.0.1:*",  # dev (`serve`, and Vite's proxy, which passes the browser's Host) and in-container
))  # fmt: skip
HOSTS = frozenset(h.strip() for h in os.environ.get("HW_ALLOWED_HOSTS", DEFAULT).split(",") if h.strip())


def allowed(host: str, hosts: frozenset[str] = HOSTS) -> bool:
    return host in hosts or f"{host.rpartition(':')[0] or host}:*" in hosts


class AllowedHosts:
    """ASGI middleware: 421 for a request whose Host is not allowed. Lifespan passes through."""

    def __init__(self, app: ASGIApp, hosts: frozenset[str] = HOSTS) -> None:
        self.app, self.hosts = app, hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not allowed(Headers(scope=scope).get("host", ""), self.hosts):
            if scope["type"] == "http":
                return await PlainTextResponse("Misdirected Request: host not allowed", 421)(scope, receive, send)
            return await WebSocketClose(code=1008)(scope, receive, send)
        await self.app(scope, receive, send)
