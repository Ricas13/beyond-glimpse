import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import catalogue_core as core  # noqa: E402
import catalogue_service as service  # noqa: E402
import catalogue_sync as sync  # noqa: E402
from test_catalogue_v2 import FakeBootstrapClient  # noqa: E402


class FailingClient(FakeBootstrapClient):
    def get_json(self, path, params=None):
        raise RuntimeError("Jellyfin went away mid-index")


class LibraryListFirstRunTests(unittest.TestCase):
    def make_db(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "catalogue-v2.db"
        connection = core.open_db(path)
        self.addCleanup(connection.close)
        return connection, path

    def test_first_bootstrap_publishes_libraries_before_indexing(self):
        connection, _ = self.make_db()
        with self.assertRaises(RuntimeError):
            sync.bootstrap(FailingClient(), connection)
        self.assertEqual([lib["id"] for lib in sync.stored_libraries(connection)], ["lib-1"])

    def test_existing_library_list_survives_a_failed_bootstrap(self):
        connection, _ = self.make_db()
        previous = [{"id": "old", "name": "Old", "media_type": "movie"}]
        sync.persist_libraries(connection, previous)
        connection.commit()
        with self.assertRaises(RuntimeError):
            sync.bootstrap(FailingClient(), connection)
        self.assertEqual([lib["id"] for lib in sync.stored_libraries(connection)], ["old"])

    def call_handle_libraries(self, path, live):
        sent = {}
        fake = SimpleNamespace(
            media_type=service.CatalogueHandler.media_type,
            cached_libraries=lambda: (_ for _ in ()).throw(live) if isinstance(live, Exception) else live,
            send_json=lambda status, payload, cache_control="no-store": sent.update(
                status=status, payload=payload, cache_control=cache_control
            ),
        )
        with patch.dict(os.environ, {"CATALOGUE_DB": str(path)}):
            service.CatalogueHandler.handle_libraries(fake, {"type": ["movie"]})
        return sent

    def test_libraries_endpoint_falls_back_to_live_list_while_indexing(self):
        connection, path = self.make_db()
        record = core.light_item_from_api({"Id": "m1", "Name": "A"}, "lib-1", "movie", 1)
        core.upsert_light_item(connection, record)
        connection.commit()
        live = [
            {"id": "lib-1", "name": "Movies", "media_type": "movie"},
            {"id": "lib-9", "name": "Shows", "media_type": "tvshow"},
        ]
        sent = self.call_handle_libraries(path, live)
        self.assertEqual(
            [(lib["name"], lib["count"]) for lib in sent["payload"]["libraries"]], [("Movies", 1)]
        )
        self.assertEqual(sent["cache_control"], "no-store")

    def test_failed_live_lookup_is_not_an_api_500(self):
        _, path = self.make_db()
        for error in (RuntimeError("bad Jellyfin response"), ValueError("invalid JSON"), AttributeError("unexpected library object")):
            sent = self.call_handle_libraries(path, error)
            self.assertEqual(sent["status"], 200)
            self.assertEqual(sent["payload"]["libraries"], [])
            self.assertEqual(sent["cache_control"], "no-store")

    def test_malformed_library_rows_are_ignored_and_only_public_fields_emitted(self):
        _, path = self.make_db()
        sent = self.call_handle_libraries(path, [
            None, "bad", {"media_type": "movie", "id": "lib1", "name": "Public", "token": "secret"}
        ])
        self.assertEqual(sent["status"], 200)
        self.assertEqual(sent["payload"]["libraries"], [
            {"id": "lib1", "name": "Public", "type": "movie", "count": 0}
        ])
        self.assertNotIn("secret", str(sent))

    def test_settled_catalogue_uses_stored_list_and_caches(self):
        connection, path = self.make_db()
        sync.persist_libraries(connection, [{"id": "lib-1", "name": "Stored", "media_type": "movie"}])
        core.set_meta(connection, "bootstrap_complete", "1")
        connection.commit()
        sent = self.call_handle_libraries(path, [{"id": "x", "name": "Live", "media_type": "movie"}])
        self.assertEqual([lib["name"] for lib in sent["payload"]["libraries"]], ["Stored"])
        self.assertEqual(sent["cache_control"], "public, max-age=300")


if __name__ == "__main__":
    unittest.main()
