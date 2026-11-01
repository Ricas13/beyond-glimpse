import os
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import catalogue_sync  # noqa: E402


def make_db(layout):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE items (id TEXT, library_id TEXT)")
    for library_id, count in layout.items():
        connection.executemany(
            "INSERT INTO items VALUES (?,?)",
            [(f"{library_id}-{n}", library_id) for n in range(count)],
        )
    return connection


class DeletionGuardTests(unittest.TestCase):
    def test_deliberately_removed_library_is_ignored(self):
        db = make_db({"a": 30, "b": 70})
        stale = [f"b-{n}" for n in range(70)]
        catalogue_sync.deletion_guard(db, stale, stage="t", ignore_library_ids=["b"])

    def test_vanished_library_is_still_blocked_by_default(self):
        db = make_db({"a": 30, "b": 70})
        stale = [f"b-{n}" for n in range(70)]
        with self.assertRaises(RuntimeError):
            catalogue_sync.deletion_guard(db, stale, stage="t")

    def test_tiny_library_single_delete_allowed(self):
        db = make_db({"a": 2, "b": 100})
        catalogue_sync.deletion_guard(db, ["a-0"], stage="t", active_library_ids=["a", "b"])

    def test_vanished_tiny_library_is_still_blocked(self):
        db = make_db({"a": 100, "b": 1})
        with self.assertRaises(RuntimeError):
            catalogue_sync.deletion_guard(db, ["b-0"], stage="t", active_library_ids=["a"])

    def test_mass_delete_in_active_library_still_blocked(self):
        db = make_db({"a": 100})
        with self.assertRaises(RuntimeError):
            catalogue_sync.deletion_guard(db, [f"a-{n}" for n in range(60)], stage="t")


class DeliberateRemovalTests(unittest.TestCase):
    PREVIOUS = '[{"id":"a","name":"Movies","media_type":"movie"},{"id":"b","name":"Kids","media_type":"movie"}]'
    CURRENT = [{"id": "a", "name": "Movies", "media_type": "movie"}]

    def run_with_env(self, **env):
        keys = ("JELLYFIN_EXCLUDE_LIBRARIES", "RECONCILE_ALLOW_LIBRARY_REMOVAL")
        saved = {k: os.environ.pop(k, None) for k in keys}
        os.environ.update(env)
        try:
            return catalogue_sync.deliberately_removed_library_ids(self.PREVIOUS, self.CURRENT)
        finally:
            for k in keys:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]

    def test_not_excluded_is_not_ignored(self):
        self.assertEqual(self.run_with_env(), set())

    def test_excluded_by_name_or_id(self):
        self.assertEqual(self.run_with_env(JELLYFIN_EXCLUDE_LIBRARIES="Kids"), {"b"})
        self.assertEqual(self.run_with_env(JELLYFIN_EXCLUDE_LIBRARIES="b"), {"b"})

    def test_explicit_override(self):
        self.assertEqual(self.run_with_env(RECONCILE_ALLOW_LIBRARY_REMOVAL="1"), {"b"})


if __name__ == "__main__":
    unittest.main()
