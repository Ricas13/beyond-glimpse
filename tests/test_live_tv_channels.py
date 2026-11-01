import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import requests  # noqa: E402

import catalogue_core as core  # noqa: E402
import catalogue_service as service  # noqa: E402


class FakeJellyfin:
    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items or [], error, []

    def resolve_user_id(self, connection=None):
        return "user-1"

    def get_json(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.error:
            raise self.error
        start = params["StartIndex"]
        return {"Items": self.items[start:start + params["Limit"]], "TotalRecordCount": len(self.items)}

    def fetch_poster(self, item_id, tag, width, quality, image_format="jpg"):
        return b"png-bytes", "image/png"


def raw(i, name, number, tag="t", program="Evening News"):
    item = {"Id": f"c{i}", "Name": name, "Number": number, "ChannelType": "TV",
            "ImageTags": {"Primary": tag} if tag else {}}
    if program:
        item["CurrentProgram"] = {"Name": program, "EpisodeTitle": "Ep", "StartDate": "2026-01-01T10:00:00Z",
                                  "EndDate": "2026-01-01T11:00:00Z"}
    return item


class LiveTvChannelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"CATALOGUE_DB": str(Path(tmp.name) / "c.db")})
        env.start()
        self.addCleanup(env.stop)
        core.open_db().close()
        saved = (service.CatalogueHandler.jellyfin, service.CatalogueHandler.channel_cache)
        self.addCleanup(lambda: (setattr(service.CatalogueHandler, "jellyfin", saved[0]),
                                 setattr(service.CatalogueHandler, "channel_cache", saved[1])))
        service.CatalogueHandler.channel_cache = {"fetched_at": 0, "channels": [], "available": False}

    def use(self, jellyfin):
        service.CatalogueHandler.jellyfin = jellyfin

    def call(self, query=None):
        sent = {}
        fake = SimpleNamespace(
            load_channels=service.CatalogueHandler.load_channels,
            send_json=lambda status, payload, cache_control="no-store": sent.update(
                status=status, payload=payload, cache_control=cache_control))
        service.CatalogueHandler.handle_channels(fake, query or {})
        return sent

    def test_channel_parsing_and_sorting(self):
        self.use(FakeJellyfin([raw(1, "Zeta", "10"), raw(2, "Alpha", "2"), raw(3, "NoNum", ""), raw(4, "Beta", "2")]))
        names = [c["name"] for c in self.call()["payload"]["channels"]]
        self.assertEqual(names, ["Alpha", "Beta", "Zeta", "NoNum"])

    def test_now_playing_logo_and_missing_logo(self):
        self.use(FakeJellyfin([raw(1, "A", "1"), raw(2, "B", "2", tag="", program="")]))
        first, second = self.call()["payload"]["channels"]
        self.assertEqual(first["logo"], "/channel-logo/c1/t.png")
        self.assertEqual(first["now"]["title"], "Evening News")
        self.assertEqual(second["logo"], "")
        self.assertIsNone(second["now"])

    def test_search_filters_by_name_and_number(self):
        self.use(FakeJellyfin([raw(1, "News One", "5"), raw(2, "Sports", "12"), raw(3, "Kids", "512")]))
        self.assertEqual([c["name"] for c in self.call({"q": ["news"]})["payload"]["channels"]], ["News One"])
        self.assertEqual(self.call({"q": ["12"]})["payload"]["total"], 2)

    def test_pagination(self):
        self.use(FakeJellyfin([raw(i, f"Ch{i:02d}", str(i)) for i in range(1, 8)]))
        page = self.call({"limit": ["3"], "offset": ["3"]})["payload"]
        self.assertEqual([c["name"] for c in page["channels"]], ["Ch04", "Ch05", "Ch06"])
        self.assertTrue(page["hasMore"])
        self.assertEqual(page["nextOffset"], 6)

    def test_no_live_tv_means_unavailable_not_an_error(self):
        self.use(FakeJellyfin(error=requests.HTTPError("404")))
        sent = self.call()
        self.assertEqual(sent["status"], 200)
        self.assertFalse(sent["payload"]["available"])
        self.assertEqual(sent["payload"]["channels"], [])

    def test_failed_refresh_keeps_last_good_list(self):
        self.use(FakeJellyfin([raw(1, "A", "1")]))
        self.assertTrue(self.call()["payload"]["available"])
        service.CatalogueHandler.channel_cache["fetched_at"] = 0  # force refresh
        self.use(FakeJellyfin(error=requests.ConnectionError("down")))
        sent = self.call()["payload"]
        self.assertTrue(sent["available"])
        self.assertEqual(sent["total"], 1)

    def test_list_is_cached_between_calls(self):
        fake = FakeJellyfin([raw(1, "A", "1")])
        self.use(fake)
        self.call(); self.call()
        self.assertEqual(len(fake.calls), 1)

    def test_empty_successful_listing_is_not_available(self):
        self.use(FakeJellyfin([]))
        result = self.call()
        self.assertEqual(result["status"], 200)
        self.assertFalse(result["payload"]["available"])
        self.assertEqual(result["payload"]["total"], 0)

    def test_channel_scan_and_response_pages_are_bounded(self):
        self.use(FakeJellyfin([raw(i, f"Channel {i}", str(i)) for i in range(20025)]))
        result = self.call({"limit": ["9999"], "offset": ["-20"]})["payload"]
        self.assertEqual(result["limit"], service.MAX_PAGE_SIZE)
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["total"], service.MAX_CHANNELS)
        self.assertEqual(len(result["channels"]), service.MAX_PAGE_SIZE)
        calls = service.CatalogueHandler.jellyfin.calls
        self.assertEqual(len(calls), 20)
        self.assertEqual(calls[-1][1]["StartIndex"], 19000)

    def test_refresh_lock_does_not_block_concurrent_channel_requests(self):
        self.use(FakeJellyfin([raw(1, "A", "1")]))
        lock = service.CatalogueHandler.channel_lock
        lock.acquire()
        try:
            sent = self.call()
        finally:
            lock.release()
        self.assertFalse(sent["payload"]["available"])
        self.assertEqual(len(service.CatalogueHandler.jellyfin.calls), 0)

    def test_malformed_channel_fields_are_safe(self):
        self.use(FakeJellyfin([
            None, {"Id": "../private", "Name": "Invalid"},
            {"Id": "c1", "Name": "A", "CurrentProgram": "bad",
             "ImageTags": "bad", "ChannelNumber": "17"},
            {"Id": "c2", "Name": "B", "ImageTags": {"Primary": "../private"}},
        ]))
        result = self.call()["payload"]
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["channels"][0]["number"], "17")
        self.assertIsNone(result["channels"][0]["now"])
        self.assertEqual(result["channels"][0]["logo"], "")
        self.assertEqual(result["channels"][1]["logo"], "")

    def test_invalid_pagination_rejected(self):
        self.use(FakeJellyfin([]))
        for invalid in ({"limit": ["wat"]}, {"offset": ["-inf"]}):
            self.assertEqual(self.call(invalid)["status"], 400)

    def test_logo_only_served_for_listed_channel_with_exact_tag(self):
        self.use(FakeJellyfin([raw(1, "A", "1", tag="good")]))
        self.call()
        results = []
        fake = SimpleNamespace(
            channel_cache=service.CatalogueHandler.channel_cache,
            jellyfin=service.CatalogueHandler.jellyfin,
            send_json=lambda status, payload, **kw: results.append(("json", status)),
            send_bytes=lambda status, body, ctype, **kw: results.append(("bytes", status, ctype)),
        )
        handle = service.CatalogueHandler.handle_channel_logo
        handle(fake, "/internal/channel-logo/c1/good.png")
        handle(fake, "/internal/channel-logo/c1/other.png")
        handle(fake, "/internal/channel-logo/zzz/good.png")
        handle(fake, "/internal/channel-logo/c1/../good.png")
        handle(fake, "/internal/channel-logo/c1/%2e%2e.png")
        self.assertEqual(results, [("bytes", 200, "image/png"), ("json", 404), ("json", 404),
                                   ("json", 404), ("json", 404)])

    def test_svg_channel_logos_are_not_served_same_origin(self):
        class SvgJellyfin(FakeJellyfin):
            def fetch_poster(self, item_id, tag, width, quality, image_format="jpg"):
                return b"<svg><script>bad()</script></svg>", "image/svg+xml"

        self.use(SvgJellyfin([raw(1, "A", "1", tag="good")]))
        self.call()
        sent = []
        fake = SimpleNamespace(
            channel_cache=service.CatalogueHandler.channel_cache,
            jellyfin=service.CatalogueHandler.jellyfin,
            send_json=lambda status, payload, **kw: sent.append(("json", status)),
            send_bytes=lambda status, body, ctype, **kw: sent.append(("bytes", status)),
        )
        service.CatalogueHandler.handle_channel_logo(
            fake, "/internal/channel-logo/c1/good.png"
        )
        self.assertEqual(sent, [("json", 502)])


if __name__ == "__main__":
    unittest.main()
