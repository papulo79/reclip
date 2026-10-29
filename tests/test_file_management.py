import unittest

from helpers import (
    AppHarness,
    post_download,
    wait_for_status,
    wait_until_idle,
)


class FileDeletionTest(unittest.TestCase):
    def setUp(self):
        self.harness = AppHarness(delay="0.05")
        self.harness.start()
        self.client = self.harness.client

    def tearDown(self):
        self.harness.stop()

    def _download(self, title="Borrar"):
        job = post_download(self.client, "https://example.com/a", title)
        return job, wait_for_status(self.client, job["job_id"], "done")

    def test_hidden_files_are_not_listed(self):
        # .jobs.json existe desde el arranque pero no debe aparecer
        self.assertTrue((self.harness.downloads / ".jobs.json").exists())
        self.assertEqual(self.client.get("/api/files").get_json(), [])

    def test_delete_removes_file_and_history(self):
        job, done = self._download()
        path = self.harness.downloads / done["filename"]
        self.assertTrue(path.exists())

        response = self.client.delete(f"/api/files/{done['filename']}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get("/api/files").get_json(), [])

        job_ids = [item["job_id"] for item in self.client.get("/api/jobs").get_json()]
        self.assertNotIn(job["job_id"], job_ids)

    def test_delete_missing_file(self):
        self.assertEqual(self.client.delete("/api/files/nada.mp4").status_code, 404)

    def test_dotfiles_cannot_be_deleted(self):
        self.assertTrue((self.harness.downloads / ".jobs.json").exists())
        self.assertEqual(self.client.delete("/api/files/.jobs.json").status_code, 400)
        self.assertTrue((self.harness.downloads / ".jobs.json").exists())

    def test_path_traversal_is_blocked(self):
        response = self.client.delete("/api/files/..%2Fapp.py")
        self.assertIn(response.status_code, (400, 404))
        self.assertTrue((self.harness.app_dir / "app.py").exists())


class ClearHistoryTest(unittest.TestCase):
    def test_clear_keeps_running_and_queued(self):
        harness = AppHarness(delay="0.05")
        harness.start()
        try:
            client = harness.client

            finished = post_download(client, "https://example.com/rapido", "Rápido")
            wait_for_status(client, finished["job_id"], "done")

            running = post_download(client, "https://example.com/slow", "Lento")
            queued = post_download(client, "https://example.com/cola", "En cola")
            self.assertEqual(running["status"], "downloading")
            self.assertEqual(queued["status"], "queued")

            response = client.delete("/api/jobs")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["deleted"], 1)

            job_ids = {item["job_id"] for item in client.get("/api/jobs").get_json()}
            self.assertNotIn(finished["job_id"], job_ids)
            self.assertIn(running["job_id"], job_ids)
            self.assertIn(queued["job_id"], job_ids)

            client.post(f"/api/cancel/{running['job_id']}")
            client.post(f"/api/cancel/{queued['job_id']}")
            wait_until_idle(client)
        finally:
            harness.stop()


if __name__ == "__main__":
    unittest.main()
