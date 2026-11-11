"""
The hosted entry point: the same tools as the local server, over HTTPS, one
private link per tester. The server makes no model calls.

A tester's connector URL is  https://<host>/c/<token>/mcp . The token picks
their database (see tenants.py). An unknown or revoked token gets a plain 404,
the same as any other missing page, so a guess reveals nothing.

Requests are stateless: no session lives on the server between calls, so there
is nothing that could carry one tester's database into another tester's request.
"""

import os
import re
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from curriculum import db, server, tenants
from curriculum.export import export_csv

# An explicit list, not "everything except". Owner-only tools that read files or
# write config on the machine (set_ingest_keywords, ingest_conversations) are
# deliberately absent: a tester's link must never reach them.
HOSTED_TOOLS = [
    server.setup_curriculum,
    server.review_interests,
    server.get_curriculum,
    server.log_topic,
    server.search_curriculum,
    server.link_topics,
    server.get_threads,
    server.update_topic,
    server.delete_topic,
    server.update_recommendation,
    server.delete_recommendation,
    server.save_recommendation,
    server.get_recommendations,
]

_PATH = re.compile(r"^/c/([A-Za-z0-9_-]+)(/.*)$")


def build_mcp() -> MCPServer:
    app = MCPServer("curriculum")
    for fn in HOSTED_TOOLS:
        app.add_tool(fn)
    return app


class TokenRouter:
    """
    ASGI wrapper. Reads the token from /c/<token>/..., looks up that tester's
    database, and holds it in server._tenant_db for the length of the request.
    """

    def __init__(self, app, hosted_root=None):
        self.app = app
        self.root = hosted_root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # lifespan etc. pass straight through
            await self.app(scope, receive, send)
            return

        match = _PATH.match(scope["path"])
        db_path = tenants.resolve(match.group(1), self.root) if match else None
        if db_path is None:
            await _not_found(send)
            return

        if match.group(2) == "/export.csv":
            await _export(scope, send, db_path)
            return

        scope = {**scope, "path": match.group(2)}
        scope["raw_path"] = scope["path"].encode()
        reset = server._tenant_db.set(db_path)
        try:
            await self.app(scope, receive, send)
        finally:
            server._tenant_db.reset(reset)


async def _export(scope, send, db_path) -> None:
    """GET <link>/export.csv: everything stored for this tester, as one CSV download."""
    if scope["method"] != "GET":
        await _respond(send, 405, b"Method not allowed", [(b"allow", b"GET")])
        return
    db.init_hosted_db(db_path)
    body = export_csv(db_path)
    filename = f"curriculum-export-{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    await _respond(send, 200, body, [
        (b"content-type", b"text/csv; charset=utf-8"),
        (b"content-disposition", f'attachment; filename="{filename}"'.encode()),
        (b"cache-control", b"no-store"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-content-type-options", b"nosniff"),
    ])


async def _respond(send, status: int, body: bytes, headers=()) -> None:
    headers = list(headers)
    if not any(k == b"content-type" for k, _ in headers):
        headers.append((b"content-type", b"text/plain; charset=utf-8"))
    headers.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _not_found(send) -> None:
    await _respond(send, 404, b"Not found")


def _security() -> TransportSecuritySettings | None:
    """
    Host-header check against DNS rebinding. Set CURRICULUM_ALLOWED_HOSTS to the
    public hostname (comma-separated for several). Unset leaves the library default.
    """
    hosts = [h.strip() for h in os.environ.get("CURRICULUM_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not hosts:
        return None
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts)


def create_app(hosted_root=None):
    inner = build_mcp().streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=_security(),
    )
    return TokenRouter(inner, hosted_root)


def main() -> None:
    import uvicorn

    host = os.environ.get("CURRICULUM_HOST", "127.0.0.1")
    port = int(os.environ.get("CURRICULUM_PORT", "8000"))
    # access_log off: request lines contain the token, and logs outlive links.
    uvicorn.run(create_app(), host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
