import json
import unittest

from helpers import AppHarness, wait_for_status


class PersistenceTest(unittest.TestCase):
    def test_done_jobs_are_restored(self):
        harness = AppHarness(delay="0.05")
        path = harness.downloads / "viejo.mp4"
        path.write_text("x")
        state = {
            "aaaaaaaaaa": {
                "status": "done",
                "url": "https://example.com/viejo",
                "title": "Viejo",
                "filename": "viejo.mp4",
                "file": str(path),
                "progress": 100,
                "created_at": "2026-01-01T00:00:00",
            },
            "_queue": [],
        }
        (harness.downloads / ".jobs.json").write_text(json.dumps(state))
        harness.start()
        try:
            jobs = harness.client.get("/api/jobs").get_json()
            self.assertEqual([job["job_id"] for job in jobs], ["aaaaaaaaaa"])
            self.assertEqual(jobs[0]["status"], "done")
            self.assertEqual(jobs[0]["filename"], "viejo.mp4")
        finally:
            harness.stop()

    def test_queued_job_is_resumed(self):
        harness = AppHarness(delay="0.05")
        state = {
            "bbbbbbbbbb": {
                "status": "queued",
                "url": "https://example.com/pendiente",
                "title": "Pendiente",
                "format": "audio",
                "progress": 0,
                "created_at": "2026-01-01T00:01:00",
            },
            "_queue": ["bbbbbbbbbb"],
        }
        (harness.downloads / ".jobs.json").write_text(json.dumps(state))
        harness.start()
        try:
            done = wait_for_status(harness.client, "bbbbbbbbbb", "done")
            self.assertEqual(done["filename"], "pendiente.mp3")
        finally:
            harness.stop()

    def test_corrupt_file_is_quarantined(self):
        harness = AppHarness()
        (harness.downloads / ".jobs.json").write_text("{esto no es json")
        harness.start()
        try:
            self.assertEqual(harness.client.get("/").status_code, 200)
            backups = list(harness.downloads.glob(".jobs.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            saved = json.loads((harness.downloads / ".jobs.json").read_text())
            self.assertEqual(saved, {"_queue": []})
        finally:
            harness.stop()


if __name__ == "__main__":
    unittest.main()
