import _isolate  # noqa: F401  (must come first)
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from curriculum import admin, db, importer, tenants
from curriculum.models import Connection, Recommendation, Topic


def counts(path):
    with sqlite3.connect(path) as c:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("topics", "connections", "recommendations"))


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.src, self.dst = d / "src.db", d / "dst.db"
        db.init_hosted_db(self.src)
        a = db.save_topic(Topic(name="Loss aversion", type="concept", tags=["poker", "psychology"], description="losses weigh more", created_at="2026-01-01T00:00:00+00:00"), self.src)
        b = db.save_topic(Topic(name="Thinking, Fast and Slow", type="book", author="Daniel Kahneman"), self.src)
        db.save_connection(Connection(from_topic_id=b.id, to_topic_id=a.id, reason="develops the idea"), self.src)
        db.save_recommendation(Recommendation(title="Atomic Habits", author="James Clear", reason="habits compound", topic_id=a.id), self.src)

    def test_imports_into_empty_target_preserving_links_and_dates(self):
        c = importer.import_graph(self.src, self.dst)
        self.assertEqual((c["topics_new"], c["connections_new"], c["recommendations_new"]), (2, 1, 1))
        self.assertEqual(counts(self.dst), (2, 1, 1))
        t = db.find_topic_by_name("loss aversion", self.dst)
        self.assertEqual((t.tags, t.created_at), (["poker", "psychology"], "2026-01-01T00:00:00+00:00"))
        threads = db.get_threads(self.dst)
        self.assertEqual([s["topic"] for s in threads[0]], ["Thinking, Fast and Slow", "Loss aversion"])
        rec = db.search_recommendations("atomic", self.dst)[0]
        self.assertEqual(db.find_topic_by_name("Loss aversion", self.dst).id, rec.topic_id)

    def test_running_twice_adds_nothing(self):
        importer.import_graph(self.src, self.dst)
        c = importer.import_graph(self.src, self.dst)
        self.assertEqual((c["topics_new"], c["connections_new"], c["recommendations_new"]), (0, 0, 0))
        self.assertEqual(counts(self.dst), (2, 1, 1))

    def test_merges_with_existing_content_and_reuses_near_duplicate_names(self):
        db.init_hosted_db(self.dst)
        mine = db.save_topic(Topic(name="the loss aversion", type="concept"), self.dst)
        db.save_topic(Topic(name="Something else", type="concept"), self.dst)
        c = importer.import_graph(self.src, self.dst)
        self.assertEqual((c["topics_new"], c["topics_reused"]), (1, 1))
        self.assertEqual(counts(self.dst)[0], 3)
        self.assertEqual(db.find_topic_by_name("Loss aversion", self.dst).id, mine.id)
        self.assertEqual(counts(self.dst)[1:], (1, 1))

    def test_dry_run_reports_but_writes_nothing(self):
        db.init_hosted_db(self.dst)
        c = importer.import_graph(self.src, self.dst, dry_run=True)
        self.assertEqual((c["topics_new"], c["connections_new"], c["recommendations_new"]), (2, 1, 1))
        self.assertEqual(counts(self.dst), (0, 0, 0))

    def test_refuses_bad_sources(self):
        with self.assertRaises(importer.ImportFailed):
            importer.import_graph(Path(self.tmp.name) / "missing.db", self.dst)
        junk = Path(self.tmp.name) / "junk.db"
        junk.write_text("not a database")
        with self.assertRaises(importer.ImportFailed):
            importer.import_graph(junk, self.dst)
        empty = Path(self.tmp.name) / "empty.db"
        sqlite3.connect(empty).close()
        with self.assertRaises(importer.ImportFailed):
            importer.import_graph(empty, self.dst)
        with self.assertRaises(importer.ImportFailed):
            importer.import_graph(self.src, self.src)

    def test_source_is_not_modified(self):
        before = self.src.read_bytes()
        importer.import_graph(self.src, self.dst)
        self.assertEqual(self.src.read_bytes(), before)


class AdminImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"CURRICULUM_HOSTED_DIR": str(Path(self.tmp.name) / "hosted")})
        env.start()
        self.addCleanup(env.stop)
        self.src = Path(self.tmp.name) / "src.db"
        db.init_hosted_db(self.src)
        db.save_topic(Topic(name="SECRET-TOPIC-NAME", type="concept", description="SECRET-DESCRIPTION"), self.src)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = admin.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_import_command_prints_counts_only_and_works(self):
        tid, token = tenants.create_tenant("someone")
        code, out, _ = self.run_cli("import", tid, str(self.src), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Would add: 1 topics", out)
        self.assertIn("nothing was written", out)
        self.assertEqual(db.count_topics(tenants.resolve(token)), 0)
        code, out, err = self.run_cli("import", tid, str(self.src))
        self.assertEqual(code, 0)
        self.assertIn("Added: 1 topics", out)
        self.assertEqual(db.count_topics(tenants.resolve(token)), 1)
        for text in (out, err):
            self.assertNotIn("SECRET", text)  # content is never printed

    def test_unknown_tester_and_bad_file_fail_cleanly(self):
        self.assertEqual(self.run_cli("import", "nosuchid", str(self.src))[0], 1)
        tid, _ = tenants.create_tenant("someone")
        code, _, err = self.run_cli("import", tid, "/nonexistent.db")
        self.assertEqual(code, 1)
        self.assertIn("Not imported", err)

    def test_removed_tester_cannot_be_imported_into(self):
        tid, _ = tenants.create_tenant("gone")
        tenants.remove_tenant(tid)
        self.assertEqual(self.run_cli("import", tid, str(self.src))[0], 1)


if __name__ == "__main__":
    unittest.main()
