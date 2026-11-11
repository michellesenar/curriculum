import sqlite3
import tempfile
import unittest
from pathlib import Path

from curriculum import db, tenants
from curriculum.models import Connection, Recommendation, Topic


class HostedSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.init_hosted_db(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_the_three_tables_and_no_source_columns(self):
        with sqlite3.connect(self.path) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual(tables, {"topics", "connections", "recommendations"})
            for t in tables:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
                self.assertNotIn("source_conversation_id", cols)

    def test_live_path_round_trip(self):
        a = db.save_topic(Topic(name="Performance under variance", type="concept", tags=["golf"]), self.path)
        b = db.save_topic(Topic(name="Opportunity cost", type="concept"), self.path)
        self.assertEqual(db.save_topic(Topic(name="the opportunity cost", type="concept"), self.path).id, b.id)
        db.save_connection(Connection(from_topic_id=a.id, to_topic_id=b.id, reason="both weigh tradeoffs"), self.path)
        rec = db.save_recommendation(Recommendation(title="Thinking in Bets", reason="decisions under uncertainty", topic_id=a.id), self.path)
        self.assertEqual(db.count_topics(self.path), 2)
        self.assertEqual(len(db.get_threads(self.path)), 1)
        self.assertEqual(db.search_recommendations("bets", self.path)[0].id, rec.id)
        self.assertIsNone(db.search_recommendations(None, self.path)[0].source_conversation_id)
        self.assertEqual(db.update_recommendation(rec.id, {"reason": "new"}, self.path).reason, "new")
        self.assertTrue(db.delete_topic("Opportunity cost", self.path))
        self.assertTrue(db.delete_recommendation(rec.id, self.path))

    def test_local_schema_still_stores_source_ids(self):
        local = Path(self.tmp.name) / "local.db"
        db.init_db(local)
        db.save_conversation(__import__("curriculum.models", fromlist=["Conversation"]).Conversation(
            id="c1", title="x", date="2026-01-01", passed_filter=True), local)
        t = db.save_topic(Topic(name="Chess", type="concept", source_conversation_id="c1"), local)
        self.assertEqual(t.source_conversation_id, "c1")


class TenantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_token_opens_own_db_only(self):
        ida, ta = tenants.create_tenant("a", self.root)
        idb, tb = tenants.create_tenant("b", self.root)
        pa, pb = tenants.resolve(ta, self.root), tenants.resolve(tb, self.root)
        self.assertNotEqual(pa, pb)
        self.assertTrue(pa.exists())
        self.assertIsNone(tenants.resolve("not-a-token", self.root))

    def test_no_raw_token_on_disk(self):
        _, token = tenants.create_tenant("a", self.root)
        for f in self.root.rglob("*"):
            if f.is_file():
                self.assertNotIn(token.encode(), f.read_bytes(), f)
            self.assertNotIn(token, f.name)

    def test_revoke_and_rotate_keep_data(self):
        tid, old = tenants.create_tenant("a", self.root)
        path = tenants.resolve(old, self.root)
        db.save_topic(Topic(name="Chess", type="concept"), path)
        new = tenants.rotate_token(tid, self.root)
        self.assertIsNone(tenants.resolve(old, self.root))
        self.assertEqual(tenants.resolve(new, self.root), path)
        self.assertEqual(db.count_topics(path), 1)
        self.assertTrue(tenants.revoke_token(new, self.root))
        self.assertFalse(tenants.revoke_token(new, self.root))
        self.assertIsNone(tenants.resolve(new, self.root))

    def test_remove_deletes_db_and_links(self):
        tid, token = tenants.create_tenant("a", self.root)
        path = tenants.resolve(token, self.root)
        self.assertTrue(tenants.remove_tenant(tid, self.root))
        self.assertFalse(path.exists())
        self.assertIsNone(tenants.resolve(token, self.root))
        self.assertEqual(tenants.list_tenants(self.root), [])
        self.assertFalse(tenants.remove_tenant(tid, self.root))

    def test_resolve_with_no_registry_is_none(self):
        self.assertIsNone(tenants.resolve("x", self.root / "nothing"))


if __name__ == "__main__":
    unittest.main()
