import _isolate  # noqa: F401  (must come first)
import contextlib
import csv
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from curriculum import admin, db, export, hosted, tenants
from curriculum.models import Connection, Recommendation, Topic


def read_csv(raw: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init_hosted_db(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self):
        a = db.save_topic(Topic(name="Performance under variance", type="concept", tags=["golf", "stats"], description="spread of outcomes"), self.path)
        b = db.save_topic(Topic(name="Café économique", type="media", author="Zoë Müller"), self.path)
        db.save_connection(Connection(from_topic_id=a.id, to_topic_id=b.id, reason="both about spread"), self.path)
        db.save_recommendation(Recommendation(title="Thinking in Bets", author="Annie Duke", reason="decisions under uncertainty", topic_id=a.id), self.path)
        db.save_recommendation(Recommendation(title="Unlinked", reason="no topic"), self.path)

    def test_empty_database_is_just_the_header(self):
        raw = export.export_csv(self.path)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # BOM, so Excel reads UTF-8
        self.assertEqual(read_csv(raw), [])
        self.assertEqual(raw.decode("utf-8-sig").strip().split(","), export.COLUMNS)

    def test_all_three_kinds_with_names_not_ids(self):
        self.fill()
        rows = read_csv(export.export_csv(self.path))
        by_kind = {k: [r for r in rows if r["kind"] == k] for k in ("topic", "connection", "recommendation")}
        self.assertEqual(len(by_kind["topic"]), 2)
        self.assertEqual(len(by_kind["connection"]), 1)
        self.assertEqual(len(by_kind["recommendation"]), 2)
        topic = next(r for r in by_kind["topic"] if r["name"].startswith("Perf"))
        self.assertEqual(topic["tags"], "golf, stats")
        self.assertEqual((by_kind["connection"][0]["from_topic"], by_kind["connection"][0]["to_topic"]),
                         ("Performance under variance", "Café économique"))
        recs = {r["title"]: r for r in by_kind["recommendation"]}
        self.assertEqual(recs["Thinking in Bets"]["topic"], "Performance under variance")
        self.assertEqual(recs["Unlinked"]["topic"], "")
        self.assertIn("Zoë Müller", [r["author"] for r in by_kind["topic"]])

    def test_commas_quotes_and_newlines_survive(self):
        db.save_topic(Topic(name="Odd", type="concept", description='says "hi", then\nnew line'), self.path)
        self.assertEqual(read_csv(export.export_csv(self.path))[0]["description"], 'says "hi", then\nnew line')

    def test_formula_cells_are_defused(self):
        db.save_topic(Topic(name="=HYPERLINK(\"http://x\")", type="concept", description="+1 and -2 and @me"), self.path)
        row = read_csv(export.export_csv(self.path))[0]
        self.assertEqual(row["name"], "'=HYPERLINK(\"http://x\")")
        self.assertEqual(row["description"], "'+1 and -2 and @me")

    def test_export_does_not_modify_the_database(self):
        self.fill()
        before = self.path.read_bytes()
        export.export_csv(self.path)
        self.assertEqual(self.path.read_bytes(), before)


class ExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _, self.ta = tenants.create_tenant("a", self.root)
        _, self.tb = tenants.create_tenant("b", self.root)
        os.environ["CURRICULUM_ALLOWED_HOSTS"] = "testserver"
        self.addCleanup(os.environ.pop, "CURRICULUM_ALLOWED_HOSTS", None)
        self.client = TestClient(hosted.create_app(self.root))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_download_headers_and_content(self):
        db.save_topic(Topic(name="Chess", type="concept"), tenants.resolve(self.ta, self.root))
        r = self.client.get(f"/c/{self.ta}/export.csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "text/csv; charset=utf-8")
        self.assertRegex(r.headers["content-disposition"], r'^attachment; filename="curriculum-export-\d{4}-\d{2}-\d{2}\.csv"$')
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual([x["name"] for x in read_csv(r.content)], ["Chess"])

    def test_only_that_testers_data(self):
        db.save_topic(Topic(name="A-only", type="concept"), tenants.resolve(self.ta, self.root))
        db.save_topic(Topic(name="B-only", type="concept"), tenants.resolve(self.tb, self.root))
        self.assertEqual([x["name"] for x in read_csv(self.client.get(f"/c/{self.tb}/export.csv").content)], ["B-only"])

    def test_bad_token_and_wrong_method(self):
        self.assertEqual(self.client.get("/c/nope/export.csv").status_code, 404)
        self.assertEqual(self.client.post(f"/c/{self.ta}/export.csv").status_code, 405)

    def test_revoked_link_cannot_export(self):
        tenants.revoke_token(self.ta, self.root)
        self.assertEqual(self.client.get(f"/c/{self.ta}/export.csv").status_code, 404)


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"CURRICULUM_HOSTED_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = admin.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def token_from(self, text):
        return text.split("/c/")[1].split("/")[0]

    def test_create_prints_working_links_once(self):
        code, out, _ = self.run_cli("create", "Yuka", "--base-url", "https://c.example.com/")
        self.assertEqual(code, 0)
        self.assertIn("https://c.example.com/c/", out)
        self.assertTrue(out.count("/mcp") == 1 and out.count("/export.csv") == 1)
        token = self.token_from(out)
        self.assertIsNotNone(tenants.resolve(token))
        _, listing, _ = self.run_cli("list")
        self.assertIn("Yuka", listing)
        self.assertNotIn(token, listing)

    def test_placeholder_host_is_flagged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CURRICULUM_PUBLIC_URL", None)
            _, out, _ = self.run_cli("create", "X")
        self.assertIn("YOUR-HOST", out)
        self.assertIn("No public address set", out)

    def test_rotate_replaces_link_and_keeps_data(self):
        _, out, _ = self.run_cli("create", "Yuka")
        old = self.token_from(out)
        tid = out.split("id ")[1].split(")")[0]
        db.save_topic(Topic(name="Chess", type="concept"), tenants.resolve(old))
        code, out2, _ = self.run_cli("rotate", tid)
        new = self.token_from(out2)
        self.assertEqual(code, 0)
        self.assertIsNone(tenants.resolve(old))
        self.assertEqual(db.count_topics(tenants.resolve(new)), 1)
        self.assertEqual(self.run_cli("rotate", "missing")[0], 1)

    def test_remove_needs_the_name_unless_yes(self):
        _, out, _ = self.run_cli("create", "Yuka")
        tid, token = out.split("id ")[1].split(")")[0], self.token_from(out)
        with mock.patch("builtins.input", return_value="nope"):
            self.assertEqual(self.run_cli("remove", tid)[0], 1)
        self.assertIsNotNone(tenants.resolve(token))
        with mock.patch("builtins.input", return_value="Yuka"):
            code, out3, _ = self.run_cli("remove", tid)
        self.assertEqual(code, 0)
        self.assertIn("14 days", out3)
        self.assertIsNone(tenants.resolve(token))
        self.assertEqual(self.run_cli("remove", tid, "--yes")[0], 1)


if __name__ == "__main__":
    unittest.main()
