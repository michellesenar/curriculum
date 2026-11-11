import _isolate  # noqa: F401  (must come first)
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from curriculum import hosted, tenants


class HealthzTests(unittest.TestCase):
    """/healthz answers platform health checks without a token and touches nothing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "hosted"  # deliberately does not exist yet
        self.client = TestClient(hosted.create_app(self.root))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_get_is_200_plain_and_needs_no_token_or_registry(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "ok\n")
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_creates_nothing_on_disk(self):
        self.client.get("/healthz")
        self.client.head("/healthz")
        self.assertFalse(self.root.exists())

    def test_head_is_200_with_empty_body(self):
        r = self.client.head("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"")

    def test_query_string_does_not_matter(self):
        self.assertEqual(self.client.get("/healthz?probe=1").status_code, 200)

    def test_other_methods_and_near_paths_stay_404(self):
        self.assertEqual(self.client.post("/healthz").status_code, 404)
        self.assertEqual(self.client.delete("/healthz").status_code, 404)
        for path in ("/healthz/", "/healthz/x", "/health", "/HEALTHZ", "/c/healthz/mcp"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.text, "Not found", path)

    def test_unknown_paths_and_bad_tokens_still_plain_404(self):
        _, token = tenants.create_tenant("a", self.root)
        for path in ("/", "/mcp", "/c/nope/mcp", "/c/nope/export.csv"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.text, "Not found", path)
        self.assertNotEqual(self.client.get(f"/c/{token}/export.csv").status_code, 404)


if __name__ == "__main__":
    unittest.main()
