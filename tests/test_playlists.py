import json
import unittest

from helpers import (
    AppHarness,
    post_download,
    wait_for_status,
)


class PlaylistInfoTest(unittest.TestCase):
    def setUp(self):
        self.harness = AppHarness()
        self.harness.start()
        self.client = self.harness.client

    def tearDown(self):
        self.harness.stop()

    def test_playlist_is_expanded(self):
        response = self.client.post(
            "/api/info", json={"url": "https://example.com/playlist?list=abc"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["type"], "playlist")
        self.assertEqual(data["title"], "Lista de prueba")
        self.assertEqual(data["entry_count"], 3)
        self.assertFalse(data["truncated"])
        self.assertEqual(
            [entry["title"] for entry in data["entries"]], ["Uno", "Dos", "Tres"]
        )
        self.assertEqual(data["entries"][0]["url"], "https://example.com/video/1")

        # Debe pedir la lista "plana" para no resolver cada vídeo
        self.assertIn("--flat-playlist", self.harness.ytdlp_calls()[-1])

    def test_video_info_keeps_contract(self):
        response = self.client.post(
            "/api/info", json={"url": "https://example.com/watch/1"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertEqual(data["type"], "video")
        self.assertEqual(data["title"], "Vídeo de prueba")
        self.assertEqual(data["formats"][0]["label"], "1080p")
        self.assertNotIn("entries", data)

    def test_playlist_entries_can_be_downloaded(self):
        info = self.client.post(
            "/api/info", json={"url": "https://example.com/playlist?list=abc"}
        ).get_json()
        entry = info["entries"][0]

        job = post_download(self.client, entry["url"], entry["title"])
        done = wait_for_status(self.client, job["job_id"], "done")
        self.assertEqual(done["filename"], "uno.mp4")
        self.assertTrue((self.harness.downloads / "uno.mp4").exists())

    def test_entries_without_url_are_skipped(self):
        playlist = {
            "_type": "playlist",
            "title": "Con huecos",
            "entries": [
                {"title": "Sin url"},
                {"url": "https://example.com/v/ok", "title": "Con url"},
            ],
        }
        harness = AppHarness(env={"FAKE_PLAYLIST_JSON": json.dumps(playlist)})
        harness.start()
        try:
            data = harness.client.post(
                "/api/info", json={"url": "https://example.com/playlist"}
            ).get_json()
            self.assertEqual(data["entry_count"], 1)
            self.assertEqual(data["entries"][0]["title"], "Con url")
        finally:
            harness.stop()


class PlaylistLimitTest(unittest.TestCase):
    def test_entries_are_capped(self):
        many = {
            "_type": "playlist",
            "title": "Larga",
            "entries": [
                {"url": f"https://example.com/v/{i}", "title": f"V{i}"}
                for i in range(150)
            ],
        }
        harness = AppHarness(env={"FAKE_PLAYLIST_JSON": json.dumps(many)})
        harness.start()
        try:
            data = harness.client.post(
                "/api/info", json={"url": "https://example.com/playlist"}
            ).get_json()
            self.assertEqual(data["entry_count"], 150)
            self.assertTrue(data["truncated"])
            self.assertEqual(len(data["entries"]), 100)
            self.assertEqual(data["entries"][0]["title"], "V0")
            self.assertEqual(data["entries"][-1]["title"], "V99")
        finally:
            harness.stop()


if __name__ == "__main__":
    unittest.main()
