import _isolate  # noqa: F401  (must come first)
import json
import os
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from curriculum import hosted, tenants

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, token, method, params=None, id=1):
    return client.post(
        f"/c/{token}/mcp",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}},
    )


class RequestLogTests(unittest.TestCase):
    """One line per tester request: what was called, never what was said."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ida, self.ta = tenants.create_tenant("a", self.root)
        os.environ["CURRICULUM_ALLOWED_HOSTS"] = "testserver"
        self.addCleanup(os.environ.pop, "CURRICULUM_ALLOWED_HOSTS", None)
        self.client = TestClient(hosted.create_app(self.root))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def logged(self, fn):
        """Run fn and return (raw log lines, parsed records)."""
        with self.assertLogs("curriculum.requests", level="INFO") as cm:
            fn()
        return cm.output, [json.loads(m.split(":", 2)[2]) for m in cm.output]

    def test_tool_call_logs_tenant_tool_status_and_outcome(self):
        _, recs = self.logged(lambda: rpc(
            self.client, self.ta, "tools/call", {"name": "get_curriculum", "arguments": {}}))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["tenant"], self.ida)
        self.assertEqual((r["event"], r["rpc"], r["tool"]), ("request", "tools/call", "get_curriculum"))
        self.assertEqual((r["status"], r["outcome"]), (200, "ok"))
        self.assertIsInstance(r["ms"], int)

    def test_neither_token_nor_content_is_ever_logged(self):
        secret = "my-very-private-opinion-about-wingspan"
        raw, _ = self.logged(lambda: rpc(
            self.client, self.ta, "tools/call",
            {"name": "log_topic", "arguments": {"name": secret, "type": "concept", "description": secret}}))
        text = "\n".join(raw)
        self.assertNotIn(self.ta, text)
        self.assertNotIn(secret, text)
        self.assertNotIn("/c/", text)

    def test_unknown_tool_name_is_not_echoed(self):
        raw, recs = self.logged(lambda: rpc(
            self.client, self.ta, "tools/call", {"name": "sneaky-text-from-client", "arguments": {}}))
        self.assertEqual(recs[0]["tool"], "unknown")
        self.assertNotIn("sneaky", "\n".join(raw))

    def test_unknown_rpc_method_is_logged_as_other(self):
        raw, recs = self.logged(lambda: rpc(self.client, self.ta, "weird/method-with-text"))
        self.assertEqual(recs[0]["rpc"], "other")
        self.assertNotIn("weird", "\n".join(raw))

    def test_failed_tool_call_is_marked_even_though_http_is_200(self):
        _, recs = self.logged(lambda: rpc(
            self.client, self.ta, "tools/call", {"name": "ingest_conversations", "arguments": {}}))
        self.assertEqual(recs[0]["status"], 200)
        self.assertIn(recs[0]["outcome"], ("tool_error", "rpc_error"))

    def test_bad_link_is_logged_without_the_path(self):
        raw, recs = self.logged(lambda: self.client.post("/c/guessed-token/mcp", headers=HEADERS, json={}))
        self.assertEqual(recs, [{"ts": recs[0]["ts"], "tenant": None, "event": "bad_link"}])
        self.assertNotIn("guessed-token", "\n".join(raw))

    def test_export_is_logged_without_the_data(self):
        _, recs = self.logged(lambda: self.client.get(f"/c/{self.ta}/export.csv"))
        self.assertEqual((recs[0]["tenant"], recs[0]["event"]), (self.ida, "export"))

    def test_healthz_logs_nothing(self):
        with self.assertNoLogs("curriculum.requests", level="INFO"):
            self.client.get("/healthz")

    def test_logging_failure_never_breaks_the_request(self):
        from unittest import mock
        with mock.patch.object(hosted._requests, "info", side_effect=RuntimeError("disk full")):
            r = rpc(self.client, self.ta, "tools/list")
        self.assertEqual(r.status_code, 200)
        self.assertIn("tools", r.json()["result"])


if __name__ == "__main__":
    unittest.main()
