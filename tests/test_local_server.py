import _isolate  # noqa: F401  (must come first)
import json
import sqlite3
import unittest

from curriculum import server
from curriculum.paths import DB_PATH


class LocalModeTests(unittest.TestCase):
    """With no tester set, the tools use the fixed local database and the full schema."""

    def test_local_calls_use_local_db_and_full_schema(self):
        self.assertIsNone(server._tenant_db.get())
        self.assertTrue(server.get_curriculum().startswith("NOT_SET_UP"))
        server.log_topic(name="Chess", type="concept", description="a game of tradeoffs")
        self.assertEqual([t["name"] for t in json.loads(server.get_curriculum())], ["Chess"])
        with sqlite3.connect(DB_PATH) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"conversations", "topic_mentions", "topics"} <= tables)
        self.assertEqual(DB_PATH.parent.name.startswith("curriculum-test-"), True)


if __name__ == "__main__":
    unittest.main()
