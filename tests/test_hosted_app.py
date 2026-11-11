import _isolate  # noqa: F401  (must come first)
import json
import os
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from curriculum import db, hosted, tenants

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, token, method, params=None, id=1, path="mcp"):
    return client.post(
        f"/c/{token}/{path}",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}},
    )


def call(client, token, tool, args=None):
    r = rpc(client, token, "tools/call", {"name": tool, "arguments": args or {}})
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert "error" not in body, body
    return body["result"]["content"][0]["text"]


class HostedAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ida, self.ta = tenants.create_tenant("a", self.root)
        self.idb, self.tb = tenants.create_tenant("b", self.root)
        os.environ["CURRICULUM_ALLOWED_HOSTS"] = "testserver"
        self.addCleanup(os.environ.pop, "CURRICULUM_ALLOWED_HOSTS", None)
        self.client = TestClient(hosted.create_app(self.root))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_unknown_and_malformed_links_are_404(self):
        for path in ("/c/nope/mcp", "/mcp", "/", "/c/", "/c/x"):
            r = self.client.post(path, headers=HEADERS, json={})
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.text, "Not found")

    def test_empty_curriculum_says_not_set_up(self):
        self.assertTrue(call(self.client, self.ta, "get_curriculum").startswith("NOT_SET_UP"))

    def test_owner_only_tools_are_not_offered_or_callable(self):
        names = {t["name"] for t in rpc(self.client, self.ta, "tools/list").json()["result"]["tools"]}
        self.assertIn("log_topic", names)
        self.assertNotIn("ingest_conversations", names)
        self.assertNotIn("set_ingest_keywords", names)
        r = rpc(self.client, self.ta, "tools/call", {"name": "ingest_conversations", "arguments": {"export_path": "/etc/passwd"}})
        self.assertTrue("error" in r.json() or r.json()["result"].get("isError"))

    def test_testers_are_isolated(self):
        call(self.client, self.ta, "log_topic", {"name": "Opportunity cost", "type": "concept", "description": "tradeoffs"})
        a = json.loads(call(self.client, self.ta, "get_curriculum"))
        self.assertEqual([t["name"] for t in a], ["Opportunity cost"])
        self.assertTrue(call(self.client, self.tb, "get_curriculum").startswith("NOT_SET_UP"))
        call(self.client, self.tb, "log_topic", {"name": "Confirmation bias", "type": "concept", "description": "x"})
        a = json.loads(call(self.client, self.ta, "get_curriculum"))
        b = json.loads(call(self.client, self.tb, "get_curriculum"))
        self.assertEqual([t["name"] for t in a], ["Opportunity cost"])
        self.assertEqual([t["name"] for t in b], ["Confirmation bias"])

    def test_hosted_db_never_gains_ingest_tables(self):
        call(self.client, self.ta, "log_topic", {"name": "Chess", "type": "concept", "description": "x"})
        call(self.client, self.ta, "search_curriculum", {"query": "chess"})
        call(self.client, self.ta, "get_threads")
        import sqlite3
        with sqlite3.connect(tenants.resolve(self.ta, self.root)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(tables, {"topics", "connections", "recommendations"})

    def test_revoked_link_stops_working_new_link_keeps_data(self):
        call(self.client, self.ta, "log_topic", {"name": "Chess", "type": "concept", "description": "x"})
        new = tenants.rotate_token(self.ida, self.root)
        self.assertEqual(rpc(self.client, self.ta, "tools/list").status_code, 404)
        self.assertEqual([t["name"] for t in json.loads(call(self.client, new, "get_curriculum"))], ["Chess"])

    def test_removed_tester_link_is_404(self):
        tenants.remove_tenant(self.idb, self.root)
        self.assertEqual(rpc(self.client, self.tb, "tools/list").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class TokenRouterConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Many requests from two testers in flight at once each see only their own database."""

    async def test_interleaved_requests_keep_their_own_database(self):
        import asyncio
        from curriculum import server

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ta = tenants.create_tenant("a", root)
            _, tb = tenants.create_tenant("b", root)
            expected = {ta: tenants.resolve(ta, root), tb: tenants.resolve(tb, root)}
            seen = []

            async def inner(scope, receive, send):
                before = server._tenant_db.get()
                await asyncio.sleep(0)  # let the other requests run in between
                await asyncio.sleep(0.001)
                seen.append((scope["path"], before, server._tenant_db.get()))
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

            router = hosted.TokenRouter(inner, root)

            async def request(token):
                sent = []

                async def send(msg):
                    sent.append(msg)

                async def receive():
                    return {"type": "http.request", "body": b""}

                await router({"type": "http", "path": f"/c/{token}/mcp"}, receive, send)
                return token, sent[0]["status"]

            results = await asyncio.gather(*[request(t) for t in [ta, tb] * 25])
            self.assertTrue(all(status == 200 for _, status in results))
            self.assertEqual(len(seen), 50)
            for path, before, after in seen:
                self.assertEqual(path, "/mcp")
                self.assertEqual(before, after)
            self.assertEqual({p for _, p, _ in seen}, set(expected.values()))
            self.assertEqual(sum(1 for _, p, _ in seen if p == expected[ta]), 25)
            self.assertIsNone(server._tenant_db.get())  # nothing leaks out of the request
