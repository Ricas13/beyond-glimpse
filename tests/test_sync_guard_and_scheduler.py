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
    def test_removed_library_is_not_blocked(self):
        db = make_db({"a": 30, "b": 70})
        stale = [f"b-{n}" for n in range(70)]
        catalogue_sync.deletion_guard(db, stale, stage="t", active_library_ids=["a"])

    def test_empty_library_list_is_blocked(self):
        db = make_db({"a": 30})
        with self.assertRaises(RuntimeError):
            catalogue_sync.deletion_guard(db, ["a-0"], stage="t", active_library_ids=[])

    def test_tiny_library_single_delete_allowed(self):
        db = make_db({"a": 2, "b": 100})
        catalogue_sync.deletion_guard(db, ["a-0"], stage="t", active_library_ids=["a", "b"])

    def test_mass_delete_in_active_library_still_blocked(self):
        db = make_db({"a": 100})
        with self.assertRaises(RuntimeError):
            catalogue_sync.deletion_guard(
                db, [f"a-{n}" for n in range(60)], stage="t", active_library_ids=["a"]
            )


if __name__ == "__main__":
    unittest.main()
