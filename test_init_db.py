import ast
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock


class InitDbTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        source = Path(__file__).resolve().parent / "app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = ast.Module(
            body=[
                node for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name in {"utcnow", "get_db", "init_db"}
            ],
            type_ignores=[],
        )
        self.hash_password = Mock(return_value="test-hash")
        self.namespace = {
            "sqlite3": sqlite3,
            "datetime": datetime,
            "timezone": timezone,
            "DB_PATH": self.db_path,
            "ADMIN_USERNAME": "overlord",
            "ADMIN_PASSWORD": "",
            "generate_password_hash": self.hash_password,
        }
        # Load only database functions, without importing or starting Flask.
        exec(compile(functions, str(source), "exec"), self.namespace)
        get_db = self.namespace["get_db"]

        @contextmanager
        def test_db():
            db = get_db()
            try:
                with db:
                    yield db
            finally:
                db.close()

        self.namespace["get_db"] = test_db
        self.namespace["init_db"]()
        self.namespace["ADMIN_PASSWORD"] = "test-only-password"

    def query(self, sql, params=()):
        db = sqlite3.connect(self.db_path)
        try:
            with db:
                return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def test_concurrent_bootstrap(self):
        barrier = threading.Barrier(2, timeout=10)

        def hash_at_same_time(password):
            barrier.wait()
            return "test-hash"

        self.hash_password.side_effect = hash_at_same_time
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.namespace["init_db"]) for _ in range(2)]
            for future in futures:
                future.result(timeout=25)
        self.assertEqual(
            self.query("SELECT username, password_hash, role, status FROM users"),
            [("overlord", "test-hash", "admin", "active")],
        )

    def test_repeated_bootstrap(self):
        self.namespace["init_db"]()
        self.namespace["init_db"]()
        self.assertEqual(self.query("SELECT COUNT(*) FROM users"), [(1,)])

    def test_existing_case_insensitive_user_keeps_identity_and_data(self):
        self.query(
            "INSERT INTO users(id, username, password_hash, role, status, created_at) "
            "VALUES(7, 'Overlord', 'old-hash', 'user', 'banned', 'original-date')"
        )
        self.query(
            "INSERT INTO favorites(user_id, item_id, created_at) VALUES(7, 'episode', 'saved-date')"
        )
        self.namespace["init_db"]()
        self.assertEqual(
            self.query("SELECT id, username, password_hash, role, status, created_at FROM users"),
            [(7, "Overlord", "test-hash", "admin", "active", "original-date")],
        )
        self.assertEqual(self.query("SELECT user_id, item_id FROM favorites"), [(7, "episode")])

    def test_empty_password_does_not_create_admin(self):
        self.namespace["ADMIN_PASSWORD"] = ""
        self.namespace["init_db"]()
        self.assertEqual(self.query("SELECT COUNT(*) FROM users"), [(0,)])
        self.hash_password.assert_not_called()

    def test_empty_password_does_not_modify_admin(self):
        self.namespace["init_db"]()
        before = self.query("SELECT * FROM users")
        self.namespace["ADMIN_PASSWORD"] = ""
        self.hash_password.reset_mock()
        self.namespace["init_db"]()
        self.assertEqual(self.query("SELECT * FROM users"), before)
        self.hash_password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
