import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ultralight_jellyfin as ultra  # noqa: E402


class IdOnlyReconciliationTests(unittest.TestCase):
    def test_only_periodic_deletion_full_sync_is_converted(self):
        original = ultra.ORIGINAL_CHOOSE_SYNC_MODE
        try:
            ultra.ORIGINAL_CHOOSE_SYNC_MODE = lambda *args: (
                "full",
                "periodic 24h deletion reconciliation is due",
            )
            fake = SimpleNamespace()
            mode, reason = ultra.ultralight_choose_sync_mode(
                fake,
                None,
                "user",
                [],
                datetime.now(timezone.utc),
            )
            self.assertEqual(mode, "incremental")
            self.assertTrue(fake._id_reconcile_due)
            self.assertIn("ID-only", reason)

            ultra.ORIGINAL_CHOOSE_SYNC_MODE = lambda *args: ("full", "forced by configuration")
            fake2 = SimpleNamespace()
            mode2, reason2 = ultra.ultralight_choose_sync_mode(
                fake2,
                None,
                "user",
                [],
                datetime.now(timezone.utc),
            )
            self.assertEqual((mode2, reason2), ("full", "forced by configuration"))
            self.assertFalse(fake2._id_reconcile_due)
        finally:
            ultra.ORIGINAL_CHOOSE_SYNC_MODE = original

    def test_id_inventory_disables_heavy_fields_images_and_user_data(self):
        calls = []

        def request_json(path, params=None):
            calls.append((path, dict(params or {})))
            return {"Items": [{"Id": "a"}, {"Id": "b"}]}

        fake = SimpleNamespace(page_size=500, request_json=request_json)
        ids = ultra.fetch_library_ids(fake, "user-1", "library-1", "movie")
        self.assertEqual(ids, ["a", "b"])
        self.assertEqual(len(calls), 1)
        path, params = calls[0]
        self.assertEqual(path, "/Items")
        self.assertEqual(params["EnableImages"], "false")
        self.assertEqual(params["EnableUserData"], "false")
        self.assertEqual(params["EnableTotalRecordCount"], "false")
        self.assertNotIn("Fields", params)
        self.assertGreaterEqual(params["Limit"], 2000)

    def test_reconciliation_deletes_missing_and_fetches_only_new_or_moved(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                library_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                media_json TEXT NOT NULL,
                poster_tag TEXT,
                backdrop_tag TEXT
            )
            """
        )
        for item_id in ("a", "b"):
            connection.execute(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, "lib", "movie", json.dumps({"id": item_id}), None, None),
            )
        connection.commit()

        fetched_detail_ids = []
        old_fetch_ids = ultra.fetch_library_ids
        old_fetch_details = ultra.fetch_items_by_ids
        try:
            ultra.fetch_library_ids = lambda self, user_id, library_id, media_type: ["a", "c"]

            def fetch_details(self, user_id, item_ids):
                fetched_detail_ids.extend(item_ids)
                return [{"Id": "c", "Name": "New C", "ImageTags": {}}]

            ultra.fetch_items_by_ids = fetch_details

            def build_entry(item, media_type, library_id):
                return {
                    "id": item["Id"],
                    "library_id": library_id,
                    "media_type": media_type,
                    "media_json": json.dumps({"id": item["Id"], "title": item["Name"]}),
                    "poster_tag": None,
                    "backdrop_tag": None,
                }

            def upsert(conn, entry):
                old = conn.execute("SELECT media_json FROM items WHERE id=?", (entry["id"],)).fetchone()
                conn.execute(
                    """
                    INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        library_id=excluded.library_id,
                        media_type=excluded.media_type,
                        media_json=excluded.media_json,
                        poster_tag=excluded.poster_tag,
                        backdrop_tag=excluded.backdrop_tag
                    """,
                    (
                        entry["id"],
                        entry["library_id"],
                        entry["media_type"],
                        entry["media_json"],
                        entry["poster_tag"],
                        entry["backdrop_tag"],
                    ),
                )
                return old is None or old[0] != entry["media_json"]

            fake = SimpleNamespace(build_catalog_entry=build_entry, upsert_catalog_entry=upsert)
            changed = ultra.run_id_reconciliation(
                fake,
                connection,
                "user",
                [{"id": "lib", "media_type": "movie", "name": "Movies"}],
            )

            rows = connection.execute("SELECT id FROM items ORDER BY id").fetchall()
            self.assertEqual(rows, [("a",), ("c",)])
            self.assertEqual(fetched_detail_ids, ["c"])
            self.assertEqual(changed, 2)
        finally:
            ultra.fetch_library_ids = old_fetch_ids
            ultra.fetch_items_by_ids = old_fetch_details
            connection.close()

    def test_reconciliation_with_no_inventory_changes_fetches_no_details(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                library_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                media_json TEXT NOT NULL,
                poster_tag TEXT,
                backdrop_tag TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO items VALUES ('a','lib','movie','{}',NULL,NULL)"
        )
        connection.commit()

        old_fetch_ids = ultra.fetch_library_ids
        old_fetch_details = ultra.fetch_items_by_ids
        try:
            ultra.fetch_library_ids = lambda self, user_id, library_id, media_type: ["a"]

            def should_not_fetch(*args, **kwargs):
                raise AssertionError("detail fetch should not run when inventory is unchanged")

            ultra.fetch_items_by_ids = should_not_fetch
            fake = SimpleNamespace(
                build_catalog_entry=lambda *args: None,
                upsert_catalog_entry=lambda *args: False,
            )
            changed = ultra.run_id_reconciliation(
                fake,
                connection,
                "user",
                [{"id": "lib", "media_type": "movie", "name": "Movies"}],
            )
            self.assertEqual(changed, 0)
        finally:
            ultra.fetch_library_ids = old_fetch_ids
            ultra.fetch_items_by_ids = old_fetch_details
            connection.close()


if __name__ == "__main__":
    unittest.main()
